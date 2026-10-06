#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
小D 桌宠 · Reasonix hook 转发器

职责只有一件：把 Reasonix 的生命周期事件转发给桌宠本体（本地回环 HTTP）。
不做判断、不读文件、不阻塞 —— 桌宠没开着也照样秒退。

RS 官方 hooks 约定（见 docs/GUIDE.md「Hooks」一节）：
  - payload 以「一行 JSON」从 stdin 传入
  - exit 0 = 放行；本脚本永远 exit 0，绝不阻断任何工具调用
  - 每次工具调用都会跑一次，所以必须极快

隐私：只取「事件名」和「工具类别」，绝不转发 prompt / toolArgs /
文件内容 / 工具输出。这些字段我们连读都不读。

用法（由 RS 调用，一般不用手动跑）：
    echo '{"event":"PreToolUse","toolName":"write_file"}' | python notify.py
自检：
    python notify.py --self-test
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time

HOST = '127.0.0.1'
PORT = int(os.environ.get('XIAOD_PET_PORT', '31726'))
PATH = '/status'

# 连接超时（秒）。本地回环正常 <5ms；桌宠没开时立刻被拒，不会等满。
TIMEOUT = 0.5

# 工具名 → 工作类别。用子串匹配，所以工具改名也不容易漏。
# 顺序有意义：先匹配到的胜出。
TOOL_KINDS = (
    (('write_file', 'edit_file', 'multi_edit', 'apply_patch', 'create',
      'delete', 'move', 'copy'), 'write'),
    (('read_file', 'grep', 'glob', 'find', 'list_dir', 'view_image',
      'list', 'stat'), 'read'),
    (('bash', 'shell', 'run_script'), 'run'),
    (('run_skill', 'task', 'subagent', 'fleet', 'parallel'), 'think'),
    (('web_fetch', 'web_search', 'fetch', 'search', 'download'), 'search'),
    (('todo_write', 'complete_step', 'ask', 'memory', 'remember'), 'plan'),
)


def kind_of(tool_name: str) -> str:
    """把工具名归到一个粗类别。只按名字判断，不看参数。"""
    n = (tool_name or '').lower()
    for names, kind in TOOL_KINDS:
        if any(x in n for x in names):
            return kind
    return 'work'


def classify(payload: dict) -> str:
    """事件 → 状态名。状态名是 hook 与桌宠之间的唯一契约。"""
    event = (payload.get('event')
             or payload.get('hook_event_name')
             or payload.get('hookEventName')
             or '')
    tool = payload.get('toolName') or payload.get('tool_name') or ''

    # ---- 会话生命周期
    if event == 'SessionStart':
        return 'awake'
    if event == 'SessionEnd':
        return 'bye'

    # ---- 一轮开始 / 结束
    if event == 'UserPromptSubmit':
        return 'thinking'
    if event == 'Stop':
        return 'done'
    if event in ('StopFailure', 'PostToolUseFailure'):
        return 'error'

    # ---- 工具调用：细分成"正在写入/查阅/执行/构思"等
    if event == 'PreToolUse':
        # ask 是"我要问用户一件事"，不是普通工具调用 ——
        # 归到 plan 的话头顶会显示"正在理清思路"，用户看不出在等他（踩过这个坑）
        if (tool or '').lower() in ('ask', 'ask_user', 'question'):
            return 'asking'
        return kind_of(tool)
    if event == 'PostToolUse':
        # 工具跑完，模型接着想 —— 但别一直挂着"思考"。
        # 交给桌宠的 IDLE_TIMEOUT 回落，这里给个轻量状态。
        return 'thinking'

    # ---- 需要用户介入
    if event in ('PermissionRequest', 'Notification'):
        return 'asking'

    # ---- 其它
    if event in ('SubagentStart', 'SubagentStop'):
        return 'think'
    if event == 'PreCompact':
        return 'organizing'
    if event == 'PostLLMCall':
        return 'thinking'
    return 'thinking'


def _post(state: str, session: str, extra: dict | None = None) -> bool:
    """手写最小 HTTP POST —— 比 urllib 轻，避免 import 开销。"""
    body = {
        'state': state,
        'session': session,
        'ts': int(time.time() * 1000),
    }
    if extra:
        body.update(extra)
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    head = (
        f'POST {PATH} HTTP/1.1\r\n'
        f'Host: {HOST}:{PORT}\r\n'
        'Content-Type: application/json; charset=utf-8\r\n'
        f'Content-Length: {len(raw)}\r\n'
        'Connection: close\r\n\r\n'
    ).encode('ascii')

    with socket.create_connection((HOST, PORT), timeout=TIMEOUT) as s:
        s.settimeout(TIMEOUT)
        s.sendall(head + raw)
        try:
            s.recv(64)              # 不关心响应内容
        except OSError:
            pass
    return True


# ---------------------------------------------------------------- 自动拉起桌宠
# 桌宠没开时，顺手把它拉起来 —— 这样"打开 RS 它自己就来了"。
# 两个必须的约束：
#   1. 限流：hook 每次工具调用都跑，不限制的话一个会话能开出一堆进程
#   2. 尊重「用户主动退出」：从菜单退掉的，不要又给它拽回来
_HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(_HERE, '.launch-lock')      # 限流时间戳
QUIT = os.path.join(_HERE, '.user-quit')        # 用户主动退出标记
LAUNCH_COOLDOWN = 120                           # 秒：两次拉起尝试的最小间隔
PY_CACHE = os.path.join(_HERE, '.python-path')  # 缓存找到的解释器


def _find_python() -> str:
    """找一个**装了 Pillow** 的 pythonw（pet.py 需要 PIL/pywin32）。

    为什么不能直接用 sys.executable：本机 PATH 上排在前面的是
    D:\\pythonw.exe（3.14），它没装 PIL，拉起 pet.py 会直接崩。
    所以依次探测候选，挑第一个能 `import PIL` 的，并把结果缓存下来
    （每次 hook 调用都探测会明显拖慢）。

    notify.py 自己只用标准库，所以**任何** Python 都能跑它 ——
    这也是 manifest 里可以用可移植的 `pyw` 的原因。
    """
    try:
        p = open(PY_CACHE, encoding='utf-8').read().strip()
        if os.path.exists(p):
            return p
    except OSError:
        pass

    cands = []
    # 1) 用户显式指定（最高优先级）
    env = os.environ.get('XIAOD_PYTHON')
    if env:
        cands.append(env)
    # 2) 当前解释器同目录（用户就是用它跑起来的）
    if sys.executable:
        cands.append(os.path.join(os.path.dirname(sys.executable),
                                  'pythonw.exe'))
    # 3) 常见安装位置 + PATH 上的启动器
    cands += [
        r'C:\Program Files\Python311\pythonw.exe',
        r'C:\Program Files\Python312\pythonw.exe',
        r'C:\Program Files\Python313\pythonw.exe',
        r'C:\Program Files\Python310\pythonw.exe',
        'pythonw', 'python', 'pyw', 'py',
    ]

    for c in cands:
        try:
            r = subprocess.run(
                [c, '-c', 'import PIL, tkinter'],
                capture_output=True, timeout=8,
                creationflags=0x08000000 if os.name == 'nt' else 0)
            if r.returncode == 0:
                try:
                    with open(PY_CACHE, 'w', encoding='utf-8') as f:
                        f.write(c)
                except OSError:
                    pass
                return c
        except Exception:
            continue
    return sys.executable or 'pythonw'


def _ensure_pet() -> None:
    """把桌宠拉起来（若它不在跑）。失败一律静默 —— 绝不能影响 RS。"""
    if os.path.exists(QUIT):
        return
    now = time.time()
    try:
        if now - os.path.getmtime(LOCK) < LAUNCH_COOLDOWN:
            return          # 刚试过，别连着开
    except OSError:
        pass
    try:
        with open(LOCK, 'w', encoding='utf-8') as f:
            f.write(str(now))
    except OSError:
        return

    # 桌宠本体位置：兼容两种布局
    #   · 插件包里：hooks/notify.py  ->  ../bin/pet.py
    #   · 工作副本：pet/notify.py    ->  ./pet.py
    for cand in (os.path.join(_HERE, 'pet.py'),
                 os.path.join(_HERE, os.pardir, 'bin', 'pet.py')):
        if os.path.exists(cand):
            pet = os.path.abspath(cand)
            break
    else:
        return
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), 'pythonw.exe')
    exe = pyw if os.path.exists(pyw) else py
    # 必须用找到的、装了 Pillow 的解释器 —— 否则 pet.py 一起来就崩
    exe = _find_python() or exe
    flags = 0
    if os.name == 'nt':
        # DETACHED_PROCESS | CREATE_NO_WINDOW：脱离父进程、不弹黑窗
        flags = 0x00000008 | 0x08000000
    try:
        subprocess.Popen(
            [exe, pet], creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    # 自检模式：灌样例事件走完整链路
    if '--self-test' in argv:
        for ev, tool in (('UserPromptSubmit', ''),
                         ('PreToolUse', 'write_file'),
                         ('PreToolUse', 'read_file'),
                         ('PermissionRequest', ''),
                         ('Stop', '')):
            st = classify({'event': ev, 'toolName': tool})
            ok = False
            try:
                ok = _post(st, 'self-test', {'kind': kind_of(tool), 'event': ev})
            except OSError:
                pass
            print(f'  {ev:<18} {tool or "-":<12} -> {st:<11} '
                  f'{"已送达" if ok else "桌宠未运行"}')
        return 0

    try:
        raw = sys.stdin.read()
    except Exception:
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    try:
        state = classify(payload)
        session = str(payload.get('sessionId') or payload.get('session_id') or '')
        tool = str(payload.get('toolName') or payload.get('tool_name') or '')
        # 诊断日志：默认关闭（设 XIAOD_HOOK_LOG=1 打开）。
        # 排查"hook 到底有没有被调用"时非常有用，平时不留文件。
        if os.environ.get('XIAOD_HOOK_LOG'):
            try:
                with open(os.path.join(_HERE, '.hook-calls.log'), 'a',
                          encoding='utf-8') as f:
                    f.write(f'{time.strftime("%H:%M:%S")} {state} '
                            f'{payload.get("event")}\n')
            except Exception:
                pass
        try:
            _post(state, session, {'kind': kind_of(tool)})
        except OSError:
            # 桌宠没开着 —— 顺手把它拉起来，下次事件就能送到了
            _ensure_pet()
    except Exception:
        # 任何意外都不许影响 RS
        pass
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        sys.exit(0)
