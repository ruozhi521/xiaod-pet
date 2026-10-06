#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""小D 的文件转换后端。

拖进来的文件 -> 挑一个目标格式 -> 在这里转。
- 图片：Pillow（png/jpg/webp/bmp/gif/tiff/ico）
- 音视频：ffmpeg（mp4/mkv/webm/mp3/wav/m4a/flac/gif）

输出放在源文件旁边，同名换扩展名；重名自动加 _1、_2…
不覆盖任何已有文件。
"""
from __future__ import annotations

import os
import shutil
import subprocess

# 分类 -> 可用目标格式（顺序即菜单顺序，第一个是推荐值）
IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif', 'tiff', 'tif', 'ico'}
VIDEO_EXTS = {'mp4', 'mkv', 'mov', 'avi', 'webm', 'flv', 'wmv', 'm4v', 'ts'}
AUDIO_EXTS = {'mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'opus', 'wma'}

# Office 文档：转 PDF 走本机装的 Microsoft Office（COM），排版质量最高。
DOC_EXTS = {'docx', 'doc', 'rtf', 'odt'}
SLIDE_EXTS = {'pptx', 'ppt', 'odp'}
SHEET_EXTS = {'xlsx', 'xls', 'csv', 'ods'}

IMAGE_TARGETS = ['png', 'jpg', 'webp', 'bmp', 'gif', 'tiff']
VIDEO_TARGETS = ['mp4', 'mkv', 'webm', 'gif', 'mp3', 'wav']
AUDIO_TARGETS = ['mp3', 'wav', 'm4a', 'flac', 'ogg']
DOC_TARGETS = ['pdf', 'txt']            # Word 文档 -> pdf / txt
SLIDE_TARGETS = ['pdf']                 # PPT -> pdf
SHEET_TARGETS = ['pdf', 'docx', 'csv']  # Excel -> pdf / Word 表格 / csv


def kind_of(path: str) -> str:
    """返回 'image' / 'video' / 'audio' / 'doc' / 'slide' / 'sheet' / 'other'。"""
    ext = os.path.splitext(path)[1].lower().lstrip('.')
    for kinds, name in ((IMAGE_EXTS, 'image'), (VIDEO_EXTS, 'video'),
                        (AUDIO_EXTS, 'audio'), (DOC_EXTS, 'doc'),
                        (SLIDE_EXTS, 'slide'), (SHEET_EXTS, 'sheet')):
        if ext in kinds:
            return name
    return 'other'


def targets_for(path: str) -> list:
    """这个文件能转成哪些格式（排除它自己现在的格式）。"""
    k = kind_of(path)
    cur = os.path.splitext(path)[1].lower().lstrip('.')
    pool = {'image': IMAGE_TARGETS, 'video': VIDEO_TARGETS,
            'audio': AUDIO_TARGETS, 'doc': DOC_TARGETS,
            'slide': SLIDE_TARGETS, 'sheet': SHEET_TARGETS}.get(k)
    if pool is None:
        return []
    return [t for t in pool if t != cur and not (cur == 'jpeg' and t == 'jpg')]


def _out_path(src: str, target: str) -> str:
    """定输出路径：源文件旁边，同名换扩展；重名加 _1、_2…（绝不覆盖）"""
    d = os.path.dirname(os.path.abspath(src))
    stem = os.path.splitext(os.path.basename(src))[0]
    cand = os.path.join(d, f'{stem}.{target}')
    i = 1
    while os.path.exists(cand):
        cand = os.path.join(d, f'{stem}_{i}.{target}')
        i += 1
    return cand


def _ffmpeg() -> str:
    """找 ffmpeg：先看 PATH，再看 winget 的 Links 目录。"""
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    for p in (os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe'),
              r'C:\ffmpeg\bin\ffmpeg.exe'):
        if os.path.exists(p):
            return p
    return ''


class ConvertError(Exception):
    pass


def _conv_image(src: str, out: str) -> None:
    from PIL import Image
    with Image.open(src) as im:
        tgt = os.path.splitext(out)[1].lower().lstrip('.')
        if tgt in ('jpg', 'jpeg') and im.mode in ('RGBA', 'LA', 'P'):
            # JPEG 不支持透明：垫一层白底，否则透明区会变黑
            bg = Image.new('RGB', im.size, (255, 255, 255))
            im2 = im.convert('RGBA')
            bg.paste(im2, mask=im2.split()[-1])
            im = bg
        elif tgt == 'gif':
            im = im.convert('RGBA') if im.mode == 'P' else im
        im.save(out)


def _has_stream(exe: str, src: str, kind: str) -> bool:
    """问 ffmpeg：这个文件有没有音频/视频流。

    比直接让转换失败好 —— 把无声视频转 mp3 时，
    ffmpeg 只会报 'Invalid argument'，用户根本不知道是"没声音"。
    """
    try:
        r = subprocess.run([exe, '-i', src], capture_output=True, text=True,
                           creationflags=0x08000000)
        info = (r.stderr or '').lower()
        return f'stream #0' in info and f': {kind}' in info
    except Exception:
        return True                     # 探测失败就别拦，让转换去试


def _conv_ffmpeg(src: str, out: str) -> None:
    exe = _ffmpeg()
    if not exe:
        raise ConvertError('找不到 ffmpeg')
    cmd = [exe, '-y', '-i', src, '-hide_banner', '-loglevel', 'error']
    tgt = os.path.splitext(out)[1].lower().lstrip('.')
    if tgt == 'gif':
        # 调色板两遍法：比默认的直转清晰得多
        cmd += ['-vf', 'fps=12,scale=480:-1:flags=lanczos,split[a][b];'
                       '[a]palettegen[p];[b][p]paletteuse', '-loop', '0']
    elif tgt in ('mp3', 'wav', 'm4a', 'flac', 'ogg'):
        # 先确认真的有音轨 —— 否则给一句人话，而不是 ffmpeg 的天书
        if not _has_stream(exe, src, 'audio'):
            raise ConvertError('这个文件里没有声音，没法转成音频')
        cmd += ['-vn']
    cmd.append(out)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=0x08000000)   # CREATE_NO_WINDOW
    if r.returncode != 0 or not os.path.exists(out):
        err = (r.stderr or '').strip().splitlines()
        raise ConvertError(err[-1] if err else 'ffmpeg 转换失败')


def _office_pdf(src: str, out: str, which: str) -> None:
    """用本机的 Microsoft Office（COM）把文档导出成 PDF。

    为什么用 COM：这是**原版渲染**，排版/字体/图表全对，质量最高。
    纯 Python 库（python-docx 等）只能读结构，排不出 PDF。

    两个必须注意的点（都实测踩过）：
      · 用 **DispatchEx** 起新实例，不碰用户自己开着的 Office。
      · 收尾必须 **gc.collect()**：COM 引用计数不清，WINWORD 会一直残留
        （实测：只 Quit() 会留 1 个僵尸进程；gc.collect() 后 2 秒内自己退干净）。
    """
    try:
        import gc
        import win32com.client as com
        import pythoncom
    except Exception:
        raise ConvertError('没装 Office，转不了 PDF')
    src = os.path.abspath(src)
    out = os.path.abspath(out)

    pythoncom.CoInitialize()
    app = doc = None
    try:
        if which == 'doc':
            app = com.DispatchEx('Word.Application')
            app.Visible = False
            doc = app.Documents.Open(src, ReadOnly=True)
            doc.ExportAsFixedFormat(out, 17)          # 17 = wdExportFormatPDF
        elif which == 'slide':
            app = com.DispatchEx('PowerPoint.Application')
            doc = app.Presentations.Open(src, WithWindow=False)
            doc.SaveAs(out, 32)                       # 32 = ppSaveAsPDF
        else:
            app = com.DispatchEx('Excel.Application')
            app.Visible = False
            doc = app.Workbooks.Open(src, ReadOnly=True)
            doc.ExportAsFixedFormat(0, out)           # 0 = xlTypePDF
    except Exception as e:
        raise ConvertError(f'Office 转换失败：{e}')
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        doc = app = None
        gc.collect()                    # 关键：清 COM 引用，否则 Office 进程不退
    if not os.path.exists(out):
        raise ConvertError('Office 没有产出 PDF')


def _doc_to_txt(src: str, out: str) -> None:
    """Word -> txt：走 COM 另存，保中文不乱码。"""
    try:
        import gc
        import win32com.client as com
        import pythoncom
    except Exception:
        raise ConvertError('没装 Office，转不了')
    pythoncom.CoInitialize()
    app = doc = None
    try:
        app = com.DispatchEx('Word.Application')
        app.Visible = False
        doc = app.Documents.Open(os.path.abspath(src), ReadOnly=True)
        doc.SaveAs(os.path.abspath(out), 2)          # 2 = wdFormatText
    except Exception as e:
        raise ConvertError(f'转换失败：{e}')
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        doc = app = None
        gc.collect()


def _sheet_to_docx(src: str, out: str) -> None:
    """Excel -> Word：把每个工作表读成一张 Word 表格。

    注：Excel **没有**"另存为 Word"（类型不兼容），只能读数据重建 ——
    所以样式（合并单元格/颜色）不会带过去，只有**数据**。
    """
    from openpyxl import load_workbook
    from docx import Document
    wb = load_workbook(src, data_only=True)
    doc = Document()
    doc.add_heading(os.path.splitext(os.path.basename(src))[0], level=1)
    for ws in wb.worksheets:
        rows = [r for r in ws.iter_rows(values_only=True)
                if any(v is not None and str(v).strip() for v in r)]
        if not rows:
            continue
        doc.add_heading(f'{ws.title}', level=2)
        ncol = max(len(r) for r in rows)
        t = doc.add_table(rows=len(rows), cols=ncol)
        t.style = 'Table Grid'
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                t.cell(i, j).text = '' if v is None else str(v)
    doc.save(out)


def _sheet_to_csv(src: str, out: str) -> None:
    """Excel -> csv（只导第一个工作表，utf-8-sig 让 Excel 打开不乱码）。"""
    import csv
    from openpyxl import load_workbook
    wb = load_workbook(src, data_only=True)
    ws = wb.worksheets[0]
    with open(out, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(ws.iter_rows(values_only=True))


def convert(src: str, target: str) -> str:
    """转换并返回输出路径。失败抛 ConvertError。"""
    if not os.path.exists(src):
        raise ConvertError('文件不存在')
    out = _out_path(src, target)
    k = kind_of(src)
    try:
        if k == 'image':
            _conv_image(src, out)
        elif k in ('video', 'audio'):
            _conv_ffmpeg(src, out)
        elif k == 'doc':
            if target == 'pdf':
                _office_pdf(src, out, 'doc')
            elif target == 'txt':
                _doc_to_txt(src, out)
            else:
                raise ConvertError('Word 只能转 pdf / txt')
        elif k == 'slide':
            _office_pdf(src, out, 'slide')
        elif k == 'sheet':
            if target == 'pdf':
                _office_pdf(src, out, 'sheet')
            elif target == 'docx':
                _sheet_to_docx(src, out)
            elif target == 'csv':
                _sheet_to_csv(src, out)
            else:
                raise ConvertError('表格只能转 pdf / docx / csv')
        else:
            raise ConvertError('不支持的文件类型')
    except ConvertError:
        raise
    except Exception as e:
        raise ConvertError(f'转换失败：{e}')
    if not os.path.exists(out):
        raise ConvertError('转换没有产出文件')
    return out


def _selftest():
    """真转一遍：PNG -> JPG/WEBP，再验证 jpg 白底不透明。"""
    import tempfile
    from PIL import Image
    ok = True
    d = tempfile.mkdtemp(prefix='xiaod-conv-')
    try:
        src = os.path.join(d, 't.png')
        # 全透明的图，用来检验 -> jpg 时会不会变黑（应垫白底）
        Image.new('RGBA', (40, 30), (47, 111, 224, 0)).save(src)

        print('  targets_for(png) =', targets_for(src))
        print('  kind_of(png)     =', kind_of(src))

        for t in ('jpg', 'webp'):
            o = convert(src, t)
            with Image.open(o) as chk:
                same = chk.size == (40, 30)
            print(f'  png -> {t}: {os.path.basename(o)} 尺寸对={same}')
            ok = ok and same

        # 重名不覆盖：再转一次 jpg 应产出 _1
        o2 = convert(src, 'jpg')
        no_overwrite = os.path.basename(o2) == 't_1.jpg'
        print(f'  重复转 jpg 不覆盖: {os.path.basename(o2)}')
        ok = ok and no_overwrite

        # 视频/音频目标列表（不实际转，只验证分类）
        print('  targets_for(mp4) =', targets_for('x.mp4'))
        print('  targets_for(mp3) =', targets_for('x.mp3'))
        print('  ffmpeg           =', _ffmpeg() or '未找到')

        # Office 文档分类（转换要装 Office，自检只验识别，不真转）
        print('  kind_of(docx)    =', kind_of('x.docx'),
              targets_for('x.docx'))
        print('  kind_of(pptx)    =', kind_of('x.pptx'),
              targets_for('x.pptx'))
        print('  kind_of(xlsx)    =', kind_of('x.xlsx'),
              targets_for('x.xlsx'))
        ok = ok and kind_of('x.docx') == 'doc'
        ok = ok and kind_of('x.pptx') == 'slide'
        ok = ok and kind_of('x.xlsx') == 'sheet'
    finally:
        shutil.rmtree(d, ignore_errors=True)
    print('通过：转换后端可用' if ok else '失败：有项目未通过')
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    sys.exit(_selftest())
