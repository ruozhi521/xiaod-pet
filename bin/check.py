# -*- coding: utf-8 -*-
"""
精灵自检：抓"孤立 / 悬空像素"这类肉眼容易漏的问题。
检查每一帧：
  1) 整幅连通性 —— 所有非空像素必须在同一个 4-邻接连通块里（8-邻接否则会把
     斜角相接也算通），有任何孤立像素块就报出来并标出坐标。
  2) 报出每个孤立块的坐标，方便直接定位。
用法: python check.py
"""
import sys

import dino

ACTIONS = dino.ALL_ACTIONS


def blobs(grid, w, h):
    seen = [[False] * w for _ in range(h)]
    out = []
    for y in range(h):
        for x in range(w):
            if grid[y][x] != '.' and not seen[y][x]:
                stack = [(x, y)]
                seen[y][x] = True
                cells = []
                while stack:
                    cx, cy = stack.pop()
                    cells.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] \
                                and grid[ny][nx] != '.':
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                out.append(cells)
    return out


def main():
    bad = 0
    for style in dino.STYLES:
        for action in ACTIONS:
            for i, layers in enumerate(dino.frames_for(dino.STYLES[style], action)):
                g = dino.merge(layers)
                bl = blobs(g, dino.W, dino.H)
                bl.sort(key=len, reverse=True)
                if len(bl) > 1:
                    bad += 1
                    print(f'[孤立像素] {style}/{action} frame {i}: '
                          f'{len(bl)} 块，块大小 {[len(b) for b in bl]}')
                    for b in bl[1:]:
                        print(f'    孤立坐标: {sorted(b)}')
    if bad:
        print(f'\n失败：发现 {bad} 处孤立/悬空像素')
        sys.exit(1)
    print('通过：所有帧都是单一连通块，没有孤立/悬空像素')


if __name__ == '__main__':
    main()
