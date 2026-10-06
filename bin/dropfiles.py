#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给 tkinter 窗口加「拖文件进来」支持（纯 ctypes，不依赖 windnd）。

Windows 的拖放靠 `WM_DROPFILES` 消息 + `DragAcceptFiles(hwnd, True)`。
tkinter 自己不处理这个消息，所以要**子类化窗口过程**（把窗口的 WndProc
换成我们的函数），收到 WM_DROPFILES 时取出路径，其余消息原样转发回去。

要点：
  · 必须子类化**顶层窗口**句柄（GetAncestor GA_ROOT），不是 canvas。
  · 换掉的旧过程一定要存好并转发，否则整个窗口会失灵。
  · 回调函数对象必须保住引用（存进 self._proc），被 GC 掉就是野指针 -> 崩溃。
"""
from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
shell32 = ctypes.WinDLL('shell32', use_last_error=True)

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
GA_ROOT = 2
GMEM_MOVEABLE = 0x0002

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowLongPtrW.restype = LRESULT
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LRESULT]
user32.CallWindowProcW.restype = LRESULT
user32.CallWindowProcW.argtypes = [LRESULT, wintypes.HWND, wintypes.UINT,
                                   wintypes.WPARAM, wintypes.LPARAM]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
shell32.DragQueryFileW.restype = wintypes.UINT
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                   wintypes.LPWSTR, wintypes.UINT]
shell32.DragFinish.restype = None
shell32.DragFinish.argtypes = [wintypes.HANDLE]


def top_hwnd_of(widget) -> int:
    """取 tkinter 控件的顶层窗口句柄（拖放必须挂在这个上面）。"""
    return user32.GetAncestor(widget.winfo_id(), GA_ROOT)


def query_drop_paths(hdrop) -> list:
    """从 HDROP 里取出所有被拖入的文件路径。"""
    n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
    out = []
    for i in range(n):
        ln = shell32.DragQueryFileW(hdrop, i, None, 0)
        buf = ctypes.create_unicode_buffer(ln + 1)
        shell32.DragQueryFileW(hdrop, i, buf, ln + 1)
        if buf.value:
            out.append(buf.value)
    shell32.DragFinish(hdrop)
    return out


def make_hdrop(paths) -> int:
    """把路径列表打包成一个 HDROP —— 自测时用来模拟"拖入"。

    结构：DROPFILES 头（20 字节）+ 宽字符路径（各自 \\0 结尾）+ 末尾额外 \\0。
    """
    body = b''.join((p + '\0').encode('utf-16-le') for p in paths) + b'\0\0'
    # pFiles, pt.x, pt.y, fNC, fWide
    head = struct.pack('<IiiII', 20, 0, 0, 0, 1)
    buf = head + body

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    # 这两个也必须声明：不声明的话 64 位句柄会被当 int 截断（OverflowError）
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
    if not h:
        return 0
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, buf, len(buf))
    kernel32.GlobalUnlock(h)
    return h


class FileDrop:
    """给一个 tkinter 顶层窗口装上拖放。用法：

        drop = FileDrop(app, on_paths)     # on_paths(list_of_paths)
        # 对象要一直活着（存成 self 属性）
    """

    def __init__(self, widget, callback):
        self.widget = widget
        self.callback = callback
        self.hwnd = top_hwnd_of(widget)
        self._proc = WNDPROC(self._wndproc)     # 必须保引用，否则野指针
        self._old = user32.SetWindowLongPtrW(
            self.hwnd, GWLP_WNDPROC,
            ctypes.cast(self._proc, ctypes.c_void_p).value)
        shell32.DragAcceptFiles(self.hwnd, True)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            try:
                paths = query_drop_paths(wparam)
                if paths:
                    self.callback(paths)
            except Exception:
                pass
            return 0
        return user32.CallWindowProcW(self._old, hwnd, msg, wparam, lparam)

    def close(self):
        try:
            shell32.DragAcceptFiles(self.hwnd, False)
            user32.SetWindowLongPtrW(self.hwnd, GWLP_WNDPROC, self._old)
        except Exception:
            pass


shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
# 自测要发消息，句柄是 64 位，同样必须声明签名
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]


def _selftest():
    """构造一个真 HDROP 发给自己，验证收发链路都通。"""
    import tkinter as tk
    got = []

    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry('120x100+60+60')
    drop = FileDrop(root, lambda ps: got.extend(ps))

    sample = [r'C:\Windows\notepad.exe', 'D:\\a b\\中文 文件.txt']
    hdrop = make_hdrop(sample)
    assert hdrop, 'make_hdrop 失败'

    root.update()
    user32.PostMessageW(drop.hwnd, WM_DROPFILES, hdrop, 0)

    for _ in range(40):
        root.update()
        root.after(25)
        import time
        time.sleep(0.025)
        if got:
            break

    ok = got == sample
    print('  模拟拖入 ->', got)
    print('  期望       ->', sample)
    print('  子类化 旧过程句柄:', bool(drop._old))
    drop.close()
    root.destroy()
    print('通过：拖放链路可用（能收到真实路径）' if ok
          else '失败：没收到路径或内容不符')
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    sys.exit(_selftest())
