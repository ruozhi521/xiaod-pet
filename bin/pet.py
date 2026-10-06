#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
小D 桌宠 —— 主程序

三件事：
  1. 一个置顶、无边框、可拖动的桌面窗口（背景透明）
  2. 监听本地端口，接收 RS hooks 通过 notify.py 送来的状态
  3. 按状态切换动画动作，并在头顶云朵里显示当前在干什么

架构（沿用 clawd-pet 的思路）：
    RS hook ──> notify.py ──POST──> 127.0.0.1:31726/status
                                            │  (后台线程，只往队列塞)
                                            ▼
                                      queue ──> tkinter 主线程切动作 + 改云朵

tkinter 不是线程安全的，所以网络线程绝不直接碰 UI，一律走队列。

运行：
    python pet.py            # 启动桌宠
    python pet.py --selftest # 不开窗口，只自检状态映射
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ------------------------------------------------------------------ 形象来源
# dino.py 的位置：兼容两种布局
#   · 插件包里：bin/pet.py  -> 同目录 bin/dino.py
#   · 工作副本：pet/pet.py  -> 上一级 xiaod-mascot/dino.py
HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (HERE, os.path.dirname(HERE)):
    if os.path.exists(os.path.join(_cand, 'dino.py')):
        sys.path.insert(0, _cand)
        break
import dino  # noqa: E402

# 拖放 + 文件转换：同目录（插件布局），或工作副本下的 plugin/bin/
for _cand in (HERE,
              os.path.join(os.path.dirname(HERE), 'plugin', 'bin')):
    if os.path.exists(os.path.join(_cand, 'dropfiles.py')):
        sys.path.insert(0, _cand)
        break
try:
    import dropfiles  # noqa: E402
    import convert    # noqa: E402
except Exception:      # 缺了也不该让桌宠起不来，只是没有拖放功能
    dropfiles = None
    convert = None

import tkinter as tk                       # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PIL import ImageTk                    # noqa: E402

# ------------------------------------------------------------------ 常量
PORT = int(os.environ.get('XIAOD_PET_PORT', '31726'))
SCALE = 3                                  # 形象缩放：比初版小一半（6 -> 3）
PAD = dino.PAD

# 透明色。tkinter 在 Windows 上只能做「纯色扣透明」，
# 所以选一个绝不会出现在形象里的洋红。
CHROMA = '#FF00FE'
CHROMA_RGB = (255, 0, 254)

WIN_W, WIN_H = 108, 106

# 形象在窗口里的位置（居中偏下）
ART_W = (dino.W + 2 * PAD) * SCALE
ART_H = (dino.H + 2 * PAD) * SCALE
ART_X = (WIN_W - ART_W) // 2
ART_Y = WIN_H - ART_H - 3

# 云朵区域（跟着形象一起缩小）
BUBBLE_H = 20
BUBBLE_Y = 2
BUBBLE_W = 100

# ------------------------------------------------------------------ 状态表
# 状态名 → (云朵文字, 动作名)
# 动作名取自 dino.ALL_ACTIONS。云朵文字就是「工作状态」。
STATES: dict[str, tuple[str, str]] = {
    'idle':       ('',              'idle'),    # 空闲：不显示云朵，只是待机
    'awake':      ('我醒啦！',        'wave'),
    'thinking':   ('正在思考…',       'think'),  # 坐在电脑前，眼睛转（不敲键盘）
    'think':      ('正在构思…',       'think'),  # 同上
    'write':      ('正在写入…',       'type'),   # 敲键盘
    'read':       ('正在查阅…',       'search'), # 举放大镜
    'run':        ('正在执行…',       'think'),  # 也在电脑前（跑命令）
    'search':     ('正在检索…',       'search'), # 举放大镜
    'plan':       ('正在理清思路…',    'idea'),   # 头顶冒灯泡
    'work':       ('正在忙…',        'type'),   # 干活 = 敲键盘
    'organizing': ('正在整理上下文…',  'coffee'), # 忙到要喝咖啡
    'asking':     ('在问你一件事',     'click'),
    'done':       ('搞定了！',        'happy'),
    'error':      ('出错了…',        'fall'),
    'bye':        ('下次见～',        'sleep'),
    'petted':     ('好舒服～',        'happy'),  # 被摸头：单独的开心态
}

# 空闲态：RS 没在干活时就是这个（不显示云朵）
DEFAULT_STATE = 'idle'

# 这些状态播一轮就回到 DEFAULT_STATE。
# 'awake'/'bye' 是短暂招呼；'petted'/'done' 是一次性动作；
# 关键：'thinking' 这类也要能回到空闲 —— 见 pet.py 的 IDLE_TIMEOUT。
TRANSIENT = {'awake', 'done', 'error', 'bye', 'petted'}

# 工作状态多久没动静就回落成空闲（秒）。
# 没有这个，一轮结束后会永远停在"正在思考…"。
IDLE_TIMEOUT = 25.0
# 动画帧间隔（毫秒）—— 8 帧一个循环，所以 55ms ≈ 18fps。
# 实测渲染一帧只要 0.1ms（图像和云朵都有缓存），所以这个值纯是节奏、
# 不是性能上限，调小不会卡。之前在 110ms（约 9fps）显得迟滞。
# 注意：它同时决定一次性动作（摸头/欢呼）的时长，见 _apply，
# 所以只在这里改，别在别处再写死一遍。
FRAME_MS = 55
# 空闲时也继续算作"工作中"的状态（收到这些就不该回落）
WORKING = {'thinking', 'think', 'write', 'read', 'run', 'search',
           'plan', 'work', 'organizing'}


# ------------------------------------------------------------------ RS 联动
# 桌宠要"随 RS 一起出现、一起消失"，就得盯着 RS 的进程。
# RS 的进程名（实测）：Reasonix Studio.exe 为主，reasonix-studio-host.exe 为后端。
# XIAOD_RS_PROC 可覆盖（分号分隔）——用于测试"RS 不在时会不会自动退"。
_env_proc = os.environ.get('XIAOD_RS_PROC')
RS_PROCESSES = tuple(
    n.strip().lower() for n in (_env_proc or
                                'reasonix studio.exe;reasonix-studio-host.exe'
                                ).split(';') if n.strip())

# 用户主动退出标记：从菜单退掉的，不要又被 notify 拽回来。
HERE_DIR = os.path.dirname(os.path.abspath(__file__))
QUIT_FLAG = os.path.join(HERE_DIR, '.user-quit')
LAUNCH_LOCK = os.path.join(HERE_DIR, '.launch-lock')

# RS 消失后等这么久才退（RS 切换会话时 host 可能短暂重启，别被吓死）
RS_GONE_GRACE = 8.0


def _rs_running() -> bool:
    """RS 是否在跑。用 psutil；它不可用时一律当作「在跑」（宁可不退也别误退）。"""
    try:
        import psutil
    except Exception:
        return True
    try:
        for p in psutil.process_iter(['name']):
            n = (p.info.get('name') or '').lower()
            if n in RS_PROCESSES:
                return True
    except Exception:
        return True
    return False


# ------------------------------------------------------------------ 全屏检测
def _fullscreen_active() -> bool:
    """前台是不是一个全屏窗口（看视频 / 玩游戏）。

    用「窗口矩形 ≈ 所在显示器矩形」判断，比看窗口样式可靠 ——
    播放器/游戏全屏的实现方式五花八门，但尺寸总是铺满。
    """
    try:
        import win32api
        import win32con
        import win32gui
    except Exception:
        return False
    try:
        h = win32gui.GetForegroundWindow()
        if not h:
            return False
        # 桌面 / 任务栏不算全屏
        if win32gui.GetClassName(h) in ('Progman', 'WorkerW', 'Shell_TrayWnd'):
            return False
        x0, y0, x1, y1 = win32gui.GetWindowRect(h)
        mon = win32api.MonitorFromWindow(h, win32con.MONITOR_DEFAULTTONEAREST)
        mx0, my0, mx1, my1 = win32api.GetMonitorInfo(mon)['Monitor']
        return (x1 - x0) >= (mx1 - mx0) - 2 and (y1 - y0) >= (my1 - my0) - 2
    except Exception:
        return False


# ------------------------------------------------------------------ 云朵绘制
def _load_font(size: int):
    for name in ('msyhbd.ttc', 'msyh.ttc', 'simhei.ttf'):
        p = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', name)
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


FONT = _load_font(9)


def draw_bubble(text: str, w: int = BUBBLE_W, h: int = BUBBLE_H) -> Image.Image:
    """画一个头顶云朵（圆角矩形 + 小尖角尾巴），返回 RGBA 图。
    尺寸随窗口一起缩放（见常量 BUBBLE_W/BUBBLE_H）。"""
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = 5
    tail = 4                                # 尾巴高度
    box = (1, 1, w - 2, h - tail - 1)
    # 云朵主体：白底 + 深色描边
    d.rounded_rectangle(box, radius=r, fill=(255, 255, 255, 255),
                        outline=dino.OUTLINE, width=1)
    # 尾巴（指向小D 的头）
    tip_x = w // 2
    d.polygon([(tip_x - 4, box[3] - 1), (tip_x + 4, box[3] - 1),
               (tip_x, box[3] + tail)],
              fill=(255, 255, 255, 255), outline=dino.OUTLINE)
    # 把尾巴内部的描边线擦掉：重画一块白色盖住接缝
    d.rectangle([tip_x - 3, box[3] - 3, tip_x + 3, box[3] - 1],
                fill=(255, 255, 255, 255))
    # 文字居中。云朵很窄，超长会溢出边界 —— 先按宽度截断加省略号。
    text = _fit_text(d, text, w - 8)
    tb = d.textbbox((0, 0), text, font=FONT)
    d.text(((w - (tb[2] - tb[0])) / 2 - tb[0],
            (box[3] - box[1] - (tb[3] - tb[1])) / 2 - tb[1] + box[1]),
           text, font=FONT, fill=(24, 34, 54, 255))
    return img


def _fit_text(d, text: str, max_w: int) -> str:
    """把文字裁到放得进云朵为止（超出加省略号）。

    转换失败的提示可能很长（如"这个文件里没有声音，没法转成音频"），
    不裁的话会顶出云朵边界、被窗口边缘切掉（踩过这个坑）。
    """
    if d.textlength(text, font=FONT) <= max_w:
        return text
    for i in range(len(text) - 1, 0, -1):
        cand = text[:i] + '…'
        if d.textlength(cand, font=FONT) <= max_w:
            return cand
    return '…'


# ------------------------------------------------------------------ HTTP 接收
class _SingleInstanceServer(HTTPServer):
    """独占端口的 HTTP 服务。

    **必须关掉 allow_reuse_address**：HTTPServer 默认是 1，而 Windows 上
    那会允许**重复绑定同一端口** —— 于是"端口被占就退出"的单实例保护
    形同虚设（实测两个实例都能绑上 31726，桌面出现两只小D、
    事件被内核随机分给其中一个）。关掉之后第二次绑定会真的 OSError。
    """
    allow_reuse_address = False


def _already_running():
    """已经有小D 在跑：提示一句再退出。

    自动拉起（hook 触发）时**不弹窗** —— 那是程序自己启动的，
    用户没做任何操作，弹个框只会莫名其妙（踩过这个坑：hook 重复拉起
    时莫名弹出"已经在跑了"）。只有用户手动双击才值得提示。
    """
    msg = (f'小D 已经在跑了（端口 {PORT} 被占用）。\n\n'
           '请看看桌面右下角的托盘图标 / 桌面上是不是已经有一只。')
    print(msg)
    if os.environ.get('XIAOD_SILENT'):
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, '小D 桌宠', 0x40)
    except Exception:
        pass


class _Handler(BaseHTTPRequestHandler):
    """只接一个 POST /status，收到就往队列塞。"""

    def do_POST(self):                       # noqa: N802
        try:
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n) if n else b'{}'
            data = json.loads(raw.decode('utf-8'))
            if self.path.rstrip('/').endswith('status') and isinstance(data, dict):
                STATE_QUEUE.put(data.get('state') or DEFAULT_STATE)
        except Exception:
            pass
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *a):               # 静音：不要刷屏
        pass


STATE_QUEUE: 'queue.Queue[str]' = queue.Queue()
# 托盘菜单的命令队列：pystray 在自己的线程里跑回调，不能直接碰 tkinter，
# 所以菜单项只往这里塞命令，主线程轮询处理。
CMD_QUEUE: 'queue.Queue[str]' = queue.Queue()


def serve_forever():
    srv = _SingleInstanceServer(('127.0.0.1', PORT), _Handler)
    srv.serve_forever()


# ------------------------------------------------------------------ 桌宠窗口
class Pet(tk.Tk):
    def __init__(self):
        super().__init__()

        self.overrideredirect(True)                     # 无边框
        self.attributes('-topmost', True)               # 置顶
        try:
            self.attributes('-transparentcolor', CHROMA)  # 纯色扣透明
        except tk.TclError:
            pass
        self.configure(bg=CHROMA)
        self.geometry(f'{WIN_W}x{WIN_H}+{self._default_x()}+{self._default_y()}')

        self.canvas = tk.Canvas(self, width=WIN_W, height=WIN_H,
                                bg=CHROMA, highlightthickness=0, bd=0)
        self.canvas.pack()

        self.state = DEFAULT_STATE
        self.frames: dict[str, list] = {}      # 动作名 -> [PhotoImage]
        self.fi = 0                            # 当前帧号
        self.until = 0.0                       # 临时动作的截止时间
        self._bubble_cache: dict[str, ImageTk.PhotoImage] = {}
        self._photo = None
        self._drag = None
        self._rs_last_seen = time.time()       # 最近一次看到 RS 的时间
        self._hidden = False                   # 是否被用户手动藏起来了
        self._auto_hidden = False              # 是否因全屏而自动收起
        self._tray = None                      # 托盘图标（pystray）
        self._tray_tip_text = '小D 桌宠'
        self._drop = None                      # 拖放钩子（dropfiles.FileDrop）
        self._busy = False                     # 正在转换：挡住重复触发

        self._build_actions()
        self._bind_events()
        self._render()
        self.after(80, self._tick)
        self.after(60, self._poll_queue)
        self.after(60, self._poll_cmd)
        self.after(3000, self._watch_rs)
        self.after(1500, self._auto_hide_for_fullscreen)
        self.after(1200, self._start_tray)
        self.after(600, self._start_drop)

    # -------------------------------------------------- 位置
    def _default_x(self):
        return self.winfo_screenwidth() - WIN_W - 40

    def _default_y(self):
        return self.winfo_screenheight() - WIN_H - 90

    # -------------------------------------------------- 预渲染动作
    def _build_actions(self):
        c = dino.STYLES[dino.CHAR]
        for action in dino.ALL_ACTIONS:
            imgs = []
            for layers in dino.frames_for(c, action):
                art = dino.render_action(c, action, layers, scale=SCALE)
                # tkinter 的 -transparentcolor 只认**纯色**：半透明像素
                # 会和洋红底混成杂色，显示成一条洋红边（type 的屏幕柔光曾踩这个坑）。
                # 所以把 alpha 阈值化 —— 也正好符合像素画的二值 alpha 本质。
                a = art.split()[-1].point(lambda v: 255 if v >= 110 else 0)
                art.putalpha(a)
                # 贴到透明色底上（tkinter 只能纯色扣透明）
                base = Image.new('RGBA', (ART_W, ART_H),
                                 CHROMA_RGB + (255,))
                base.alpha_composite(art, (0, 0))
                imgs.append(ImageTk.PhotoImage(base.convert('RGB')))
            self.frames[action] = imgs

    # -------------------------------------------------- 事件绑定（互动）
    def _bind_events(self):
        self.canvas.bind('<Button-1>', self._on_press)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Double-Button-1>', lambda e: self.set_state('petted'))
        self.canvas.bind('<Button-3>', self._menu)

    def _on_press(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y(), False)

    def _on_drag(self, e):
        if not self._drag:
            return
        ox, oy, moved = self._drag
        nx, ny = e.x_root - ox, e.y_root - oy
        if abs(nx - self.winfo_x()) > 3 or abs(ny - self.winfo_y()) > 3:
            moved = True
        self._drag = (ox, oy, moved)
        self.geometry(f'+{nx}+{ny}')

    def _on_release(self, e):
        # 没拖动 = 点了一下 → 摸头：开心一下
        if self._drag and not self._drag[2]:
            self.set_state('petted')
        self._drag = None

    def _menu(self, e):
        m = tk.Menu(self, tearoff=0)
        m.add_command(label='摸头', command=lambda: self.set_state('petted'))
        m.add_separator()
        m.add_command(label='藏起来', command=self._hide)
        m.add_separator()
        m.add_command(label='退出', command=lambda: self._quit(silent=False))
        m.tk_popup(e.x_root, e.y_root)

    # -------------------------------------------------- 状态
    def set_state(self, state: str):
        """线程安全入口：只往队列塞，真正处理在主线程。"""
        STATE_QUEUE.put(state)

    # -------------------------------------------------- 隐藏 / 召回
    def _hide(self, e=None):
        """藏起来。手动藏 = 一直藏着，直到从托盘叫回来。"""
        self._hidden = True
        self.withdraw()                     # 连任务栏条目一起收掉
        self._tray_tip('小D 藏起来了，右键托盘图标叫它回来')

    def _show(self, e=None):
        """叫回来。"""
        self._hidden = False
        self.deiconify()
        self.attributes('-topmost', True)
        self.set_state('awake')

    def _toggle(self, e=None):
        (self._show if self._hidden else self._hide)()

    def _auto_hide_for_fullscreen(self):
        """全屏时自动收起、退出全屏自动回来。

        和手动隐藏分开记（_auto_hidden），否则"全屏时手动藏过"就再也不会自己回来了。
        """
        if self._hidden:
            return                          # 手动藏了就别管
        full = _fullscreen_active()
        if full and not self._auto_hidden:
            self._auto_hidden = True
            self.withdraw()
        elif not full and self._auto_hidden:
            self._auto_hidden = False
            self.deiconify()
            self.attributes('-topmost', True)
        self.after(1500, self._auto_hide_for_fullscreen)

    def _tray_tip(self, text: str):
        """给托盘图标换提示文字（没有托盘就静默）。"""
        try:
            if self._tray is not None:
                self._tray.title = '小D 桌宠'
                self._tray_tip_text = text
        except Exception:
            pass


    # -------------------------------------------------- 拖文件进来
    def _start_drop(self):
        """给窗口装上拖放。缺 dropfiles 就静默跳过。"""
        if dropfiles is None:
            return
        try:
            self._drop = dropfiles.FileDrop(self, self._on_drop)
        except Exception:
            self._drop = None

    def _on_drop(self, paths):
        """收到拖入的文件 —— 弹菜单让用户选目标格式。"""
        if self._busy:
            return
        if convert is None:
            self.set_state('error')
            return

        usable = [p for p in paths if convert.targets_for(p)]
        if not usable:
            self.set_state('error')          # 不支持的类型：出错动作
            self._toast('这个类型我还不会转…')
            return

        if len(usable) == 1:
            self._drop_menu(usable[0])
        else:
            # 多个文件：按类型分组，一次转一类
            kinds = {}
            for p in usable:
                kinds.setdefault((convert.kind_of(p),
                                  tuple(convert.targets_for(p))), []).append(p)
            m = tk.Menu(self, tearoff=0)
            for (k, tgts), group in kinds.items():
                sub = tk.Menu(m, tearoff=0)
                for t in tgts:
                    sub.add_command(
                        label=t.upper(),
                        command=lambda g=group, tt=t: self._do_convert(g, tt))
                m.add_cascade(label=f'{len(group)} 个文件 ->', menu=sub)
            m.add_separator()
            m.add_command(label='取消')
            m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _drop_menu(self, path: str):
        tgts = convert.targets_for(path)
        m = tk.Menu(self, tearoff=0)
        name = os.path.basename(path)
        short = name if len(name) <= 22 else name[:19] + '…'
        m.add_command(label=short, state='disabled')
        m.add_separator()
        for t in tgts:
            m.add_command(label=f'转成 {t.upper()}',
                          command=lambda tt=t: self._do_convert([path], tt))
        m.add_separator()
        m.add_command(label='取消')
        m.tk_popup(self.winfo_pointerx(), self.winfo_pointery())

    def _do_convert(self, paths, target: str):
        """真转。放后台线程，转完回主线程汇报。"""
        if self._busy:
            return
        self._busy = True
        self.set_state('run')
        # Office 转 PDF 要 5~8 秒（是真的在启动 Office），
        # 不提示的话用户会以为卡死了 —— 云朵先挂一句"转换中"。
        need_office = any(convert.kind_of(p) in ('doc', 'slide', 'sheet')
                          for p in paths)
        self._toast('正在转格式，稍等…' if need_office else '转换中…', secs=60)

        def work():
            done, failed = [], []
            for p in paths:
                try:
                    out = convert.convert(p, target)
                    done.append(out)
                except Exception as e:
                    failed.append(f'{os.path.basename(p)}: {e}')
            self.after(0, lambda: self._convert_done(done, failed))

        threading.Thread(target=work, daemon=True).start()

    def _convert_done(self, done, failed):
        self._busy = False
        if failed and not done:
            self.set_state('error')
            # 错误原因本身就是人话（见 convert.py），别再堆"没转成：+文件名"，
            # 云朵只有 100px 宽，前缀会把真正有用的信息挤出画面。
            self._toast(str(failed[0]).split(': ', 1)[-1])
        else:
            self.set_state('done')           # 搞定了！
            if done:
                d = os.path.dirname(done[0])
                self._toast(f'转好了 {len(done)} 个')
                try:
                    os.startfile(d)          # 顺手把文件夹打开
                except Exception:
                    pass

    def _toast(self, text: str, secs: float = 4.0):
        """在云朵里临时显示一句话（转换结果等），几秒后自动消失。

        同时把**状态**也保持这么久 —— 否则状态先回落成空闲、
        云朵却还挂着提示，动作和文字对不上（踩过这个坑）。
        """
        self._custom_text = text
        self._custom_until = time.time() + secs    # 不设这个会被立刻清掉
        self.until = max(self.until, self._custom_until)

    def _apply(self, state: str):
        if state not in STATES:
            state = DEFAULT_STATE
        self.state = state
        self.fi = 0
        now = time.time()
        if state in TRANSIENT:
            # 一次性动作（摸头/欢呼/问好）：播一轮就回落。
            # FRAME_MS/帧（见 _tick），所以一轮 ≈ n*FRAME_MS；再多留 1s 余韵。
            n = len(self.frames.get(STATES[state][1], [1]))
            self.until = now + max(0.9, n * FRAME_MS / 1000.0) + 1.0
            # 若有 toast 在显示，别让它被这轮动作提前掐断（否则云朵文字和
            # 动作时长对不上 —— 文字还在、状态已经回落了）
            if getattr(self, '_custom_text', None):
                self.until = max(self.until,
                                 getattr(self, '_custom_until', 0))
        elif state in WORKING:
            # 工作状态：如果这么久没有新事件，说明 RS 已经干完了
            # —— 否则一轮结束后会永远停在"正在思考…"
            self.until = now + IDLE_TIMEOUT
        else:
            self.until = 0.0

    def _poll_queue(self):
        latest = None
        try:
            while True:
                latest = STATE_QUEUE.get_nowait()
        except queue.Empty:
            pass
        if latest:
            if os.path.exists(QUIT_FLAG):
                try:
                    os.remove(QUIT_FLAG)   # 有新事件 = 用户又用 RS 了，取消"退出"状态
                except OSError:
                    pass
            self._apply(latest)
        self.after(60, self._poll_queue)

    # -------------------------------------------------- 托盘菜单命令
    def _poll_cmd(self):
        """处理托盘菜单发来的命令（托盘在别的线程，只能走队列）。"""
        try:
            while True:
                cmd = CMD_QUEUE.get_nowait()
                if cmd == 'show':
                    self._show()
                elif cmd == 'hide':
                    self._hide()
                elif cmd == 'quit':
                    self._quit(silent=False)
                elif cmd == 'wave':
                    self.set_state('petted')
        except queue.Empty:
            pass
        self.after(60, self._poll_cmd)

    # -------------------------------------------------- 托盘图标
    def _tray_image(self):
        """用形象本身做托盘图标（缩到 64px，取一张静帧）。"""
        c = dino.STYLES[dino.CHAR]
        art = dino.render_action(c, 'idle', dino.frames_for(c, 'idle')[0],
                                 scale=2)
        bg = Image.new('RGBA', art.size, (0, 0, 0, 0))
        bg.alpha_composite(art)
        return bg.resize((64, 64), Image.LANCZOS)

    def _start_tray(self):
        """起托盘图标。pystray 缺失也不影响桌宠本体。"""
        try:
            import pystray
        except Exception:
            return                          # 没装就跳过，桌宠照常跑
        try:
            def _cmd(name):
                return lambda *a: CMD_QUEUE.put(name)

            menu = pystray.Menu(
                pystray.MenuItem('叫回小D', _cmd('show'), default=True),
                pystray.MenuItem('藏起来', _cmd('hide')),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('摸头', _cmd('wave')),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem('退出', _cmd('quit')),
            )
            self._tray = pystray.Icon('xiaod', self._tray_image(),
                                      self._tray_tip_text, menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
        except Exception:
            self._tray = None

    # -------------------------------------------------- 跟随 RS 生命周期
    def _watch_rs(self):
        """RS 在就跑着，RS 关了（并等过宽限期）自己也退。

        宽限期是必需的：RS 切换会话/重启 host 时会有一瞬间查不到进程，
        没有宽限期就会跟着一起闪退。
        """
        if _rs_running():
            self._rs_last_seen = time.time()
        elif time.time() - self._rs_last_seen > RS_GONE_GRACE:
            self._quit(silent=True)        # RS 没了 —— 不算"用户主动退出"
            return
        self.after(3000, self._watch_rs)

    def _quit(self, silent: bool = False):
        """退出。silent=True 用于 RS 关闭导致的自动退出。"""
        if not silent:
            try:
                with open(QUIT_FLAG, 'w', encoding='utf-8') as f:
                    f.write(str(time.time()))   # 记住：是用户自己要关的
            except OSError:
                pass
        # 托盘图标必须显式停掉，否则它会在托盘里留个死图标
        try:
            if self._tray is not None:
                self._tray.stop()
        except Exception:
            pass
        self.destroy()

    # -------------------------------------------------- 动画循环
    def _tick(self):
        if self.until and time.time() > self.until:
            self._apply(DEFAULT_STATE)
        action = STATES.get(self.state, STATES[DEFAULT_STATE])[1]
        frames = self.frames.get(action) or []
        if frames:
            self.fi = (self.fi + 1) % len(frames)
        self._render()
        self.after(FRAME_MS, self._tick)

    # -------------------------------------------------- 绘制
    def _render(self):
        action = STATES.get(self.state, STATES[DEFAULT_STATE])[1]
        frames = self.frames.get(action) or []
        art = frames[self.fi % len(frames)] if frames else None

        # 自定义提示（转换结果等）优先于状态文案，显示几秒后自动消失
        ct = getattr(self, '_custom_text', None)
        if ct and time.time() > getattr(self, '_custom_until', 0):
            self._custom_text = None
            ct = None
        text = ct or STATES.get(self.state, STATES[DEFAULT_STATE])[0]
        bubble = None
        if text:                      # 空闲态文字为空 -> 不画云朵
            bubble = self._bubble_cache.get(text)
            if bubble is None:
                bub = draw_bubble(text)
                base = Image.new('RGBA', (WIN_W, BUBBLE_H + 10),
                                 CHROMA_RGB + (255,))
                base.alpha_composite(bub, ((WIN_W - bub.width) // 2, 0))
                bubble = ImageTk.PhotoImage(base.convert('RGB'))
                self._bubble_cache[text] = bubble

        self.canvas.delete('all')
        if bubble is not None:
            self.canvas.create_image(WIN_W // 2, BUBBLE_Y, image=bubble,
                                     anchor='n')
        if art is not None:
            self.canvas.create_image(ART_X, ART_Y, image=art, anchor='nw')
        self._photo = (bubble, art)      # 防 GC


# ------------------------------------------------------------------ 自检
def selftest() -> int:
    bad = 0
    print('=== 状态 -> 云朵文字 / 动作 ===')
    for st, (text, action) in STATES.items():
        ok = action in dino.ALL_ACTIONS
        if not ok:
            bad += 1
        print(f'  {st:<11} {text:<12} {action:<8} {"OK" if ok else "动作不存在!"}')
    print()
    print('=== 云朵绘制 ===')
    for st in ('thinking', 'write', 'asking', 'done'):
        img = draw_bubble(STATES[st][0])
        print(f'  {STATES[st][0]:<12} {img.size} OK')
    print()
    if bad:
        print(f'失败：{bad} 个状态映射到了不存在的动作')
        return 1
    print('通过：所有状态都能映射到 dino 里已有的动作，云朵绘制正常')
    return 0


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        sys.exit(selftest())

    # 先占端口：被占用说明已经有一只小D 在跑。
    # 若放任不管，会开出第二个窗口却收不到事件（端口已被先来的那只占用），
    # 看起来像"桌宠坏了"。所以这里直接报错退出，并弹窗提示（pythonw 无控制台）。
    #
    # 注意：必须显式关掉 allow_reuse_address！HTTPServer 默认是 1
    # （Windows 上允许重复绑定同一端口），那样这个保护**根本不生效** ——
    # 实测两个实例都能绑上 31726，桌面出现两只小D、事件随机分给其中一个。
    try:
        srv = _SingleInstanceServer(('127.0.0.1', PORT), _Handler)
    except OSError:
        _already_running()
        sys.exit(1)

    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f'小D 桌宠已启动，监听 http://127.0.0.1:{PORT}/status')
    Pet().mainloop()
