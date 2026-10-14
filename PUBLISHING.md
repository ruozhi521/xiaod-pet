# 发布指引（小D 桌宠）

面向维护者。发布入口在 **Reasonix Studio 界面里**，
`reasonix plugin install / doctor` 这些 CLI **只能本地验证，不能提交、不能过审**。

## 一、发布前本地自检

```sh
cd <本目录>

# 1) 语法
python -m py_compile bin/pet.py bin/convert.py bin/dino.py bin/dropfiles.py hooks/notify.py

# 2) 形象自检（像素连通性）
python bin/check.py

# 3) 桌宠自检（状态→动作映射）
python bin/pet.py --selftest

# 4) 转换后端自检
python bin/convert.py

# 5) 插件清单能否被 RS 接受
reasonix plugin install . --dry-run
```

期望：`ok=true`、`hookCount=7`、`compatibility=full`。

## 二、推到 GitHub（fixed source）

发布表单要一个**固定来源**，插件包必须是 **commit 锁定**的 GitHub tree URL：

```
https://github.com/<OWNER>/<REPO>/tree/<40位完整commit>/<包所在的子目录>
```

> 注意：**本地路径、未推送的 commit 都不算公开来源**。
> 也**不能**用 `SKILL.md` 直链 —— 那种只装单个文件，
> 本插件的 `bin/` 同级文件不会跟着过去（它需要兄弟文件）。

```sh
git remote add origin https://github.com/<OWNER>/<REPO>.git
git push -u origin main

# 取 40 位 commit
git rev-parse HEAD
```

改动后发布新版本：改 `reasonix-plugin.json` 里的 `version`，
重新走一遍上面的自检，提交推送，再取新的 commit ID。

## 三、提交到社区市场

1. 登录 Studio，打开 **Settings → Extensions → Discover → Publish**
2. 选择 kind = **plugin**
3. 填写表单：

| 字段 | 填什么 |
| --- | --- |
| Name | `xiaod-pet` |
| Version | `1.0.2`（**必须**和 `reasonix-plugin.json` 里的 `version` 完全一致） |
| Fixed source | 上面那个 **commit 锁定的 GitHub tree URL** |
| Summary | 一句话：`Reasonix Studio 的像素桌宠：状态云朵 + 拖文件转格式` |
| Description | 见 `README.md` 的内容 |
| Repository URL | `https://github.com/<OWNER>/<REPO>` |
| Tags | `desktop-pet`, `pixel`, `status`, `hooks`, `file-convert`, `windows` |

4. **先选 Private** 存下来 → 在 **My packages** 下自己装一遍试。
5. 试好了再提交公开审核（进入审核队列）。

### 状态说明

| 状态 | 含义 |
| --- | --- |
| Private | 只你自己可见，可先自测，之后能再提交审核 |
| 已提交 | 进了审核队列。**提交 ≠ 通过 ≠ 别人能装** |
| 审核通过 | reviewer 会记录一个 content digest |
| 有 digest | 用户可直接安装 |
| 无 digest | 用户安装时会看到 **Trust and install** 确认框 |

审核被拒 → 改源码 → **升版本号** → 重新提交。

## 四、更新版本

1. 改代码
2. 升 `reasonix-plugin.json` 的 `version`
3. 重跑「一、发布前本地自检」
4. commit + push，取新 commit ID
5. 在市场表单提交新版本

> 用户**不会被动升级**，他们自己选择要不要更新。

## 五、仓库里不该提交的东西

见 `.gitignore`。运行时文件（`__pycache__/`、`.python-path`、
`.launch-lock`、`.user-quit`、`.hook-calls.log`）都是自动生成的，别提交。

## 六、发布前必须确认的坑（都实际踩过）

1. **不要写死解释器路径**。`reasonix-plugin.json` 的 `command` 用
   `pyw` / `pythonw` 这类可移植启动器；桌宠本体需要 Pillow，
   由 `hooks/notify.py` 的 `_find_python()` 自动去找装了 Pillow 的解释器。
   （曾 7 处写死 `C:\Program Files\Python311\pythonw.exe`，别人电脑直接全废。）
2. **`HTTPServer` 默认 `allow_reuse_address = 1`**，在 Windows 上允许重复绑定
   同一端口，所以单实例保护必须显式关掉（见 `bin/pet.py` 的
   `_SingleInstanceServer`）。
3. **Office 转 PDF 后要 `gc.collect()`**，否则 WINWORD 进程会残留。
4. **依赖要在 README 里写清**：必需 Pillow/tkinter；
   可选 pywin32/psutil/pystray/openpyxl/python-docx/win32com+Office/ffmpeg。
5. **状态文件必须有生命周期，不能只创建不删除**。踩过一个致命的：
   `.launch-lock` 只为限流而建，却从不删除。RS 一更新（→ RS 重启 →
   `pet.py` 检测到 RS 消失后自杀），锁却留在磁盘上；下次 hook 撞上
   `FileExistsError` 时只刷新时间戳就 return，于是**永远不再拉起桌宠**
   —— 表现是"每次 RS 更新后小D 就再也不出现"，且完全无报错。
   修法：撞锁时先确认进程真的没在跑，再把陈旧锁删掉重抢。
   → 凡是 `O_CREAT|O_EXCL` 的锁，都要配一条"陈旧锁自愈"路径。
6. **hook 的 manifest 写法：用 shell form，不要用 exec form**。
   RS 更新后 `{"command":"pythonw","args":["hooks/notify.py"]}` 这种
   exec form **不再被执行**（静默失效，无任何日志）。改成
   `{"command":"pythonw hooks/notify.py || pyw hooks/notify.py || py hooks/notify.py","shell":"cmd"}`
   才生效。二分实验实测：exec form ❌ / shell form ✅。
7. **排查 hook 时，`.launch-lock` 和"日志无记录"都不是判据**。
   守卫提前 return 时同样不建锁；而诊断日志默认是关的（要设
   `XIAOD_HOOK_LOG=1` 才写）。要用**进程探针**（记录 hook 由哪个父进程
   启动）来判断 hook 到底有没有跑 —— 这个坑我踩过两次。
