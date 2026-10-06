# -*- coding: utf-8 -*-
"""
小D —— 专属像素形象（桌宠版）：分层像素精灵 + 程序化动作
形象：幼态小恐龙（大头大眼短腿，像小火龙那版）。
做法参考 clawd 桌宠项目：单一真相源的像素网格 + 分层，
动作靠组合图层程序化生成，加新动作不用重画整只。

字符: . 透明 / b 主色 / l 肚皮 / d 暗色 / k 瞳 / W 眼白 / r 腮红 / m 嘴 / o 道具
"""
import math
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SCALE = 16
CHAR = 'baby'       # 小D 的形象（唯一）
PAD = 1
W, H = 26, 22

# ---------------------------------------------------------------- 精细网格
# 设计还是画在 W×H 的格子上（坐标全不用改），但**渲染**时铺到 SS 倍细的
# 网格上：1 个设计格 = SS×SS 的子格。
#   · 外观不变：每个设计格仍是 SS×SS 的实心块，像素味照旧
#   · 位移精度提高 SS 倍：图层可以带 .sub 亚格偏移（如 (0, 0.5)），
#     于是手臂能"半格"动 —— 这是灵动感的来源（clawd 靠矢量 transform
#     做到亚像素；我们用加密网格达到类似效果，且**边缘依然对齐网格、不糊**）。
SS = 2
GW, GH = W * SS, H * SS          # 精细网格尺寸
SCALE_FINE = SCALE // SS         # 每个精细格的像素数（16//2 = 8）
PADF = PAD * SS                  # 精细网格的留白

OUTLINE = (14, 26, 50, 255)
COL = {
    'b': (47, 111, 224),    # #2F6FE0
    'l': (126, 174, 255),   # 肚皮
    'd': (20, 60, 132),     # 脚趾 / 尾底阴影
    'k': (16, 26, 46),      # 瞳
    'W': (255, 255, 255),   # 高光
    'r': (255, 138, 166),   # 腮红
    'm': (16, 26, 46),      # 嘴
    'o': (255, 158, 74),    # 道具（拿在手里的东西）
    'n': (120, 134, 162),   # 笔电机身（中灰蓝：太暗会糊成一团黑，看不出是键盘）
    'T': (170, 122, 78),    # 桌面（木色：和蓝/灰都分得开，一眼看出是桌子）
    't': (112, 78, 52),     # 桌面厚度 / 桌腿（木色的暗部）
    'y': (255, 214, 102),   # 特效·灯泡（灵感）——暖黄，和主色蓝互补，最跳
    'c': (150, 100, 60),    # 特效·咖啡（忙碌）
    'g': (198, 208, 222),   # 特效·热气 / 镜片反光（浅灰白，飘起来才看得见）
    'S': (208, 240, 255),   # 屏幕亮面（冷白，看着像在发光）
    's': (168, 224, 250),   # 屏幕光映在小D 身上的边缘光（偏青，和主色分得开）
}


class Layer:
    def __init__(self, name=''):
        self.name = name
        self.g = [['.'] * W for _ in range(H)]
        # 亚格偏移（单位 = 设计格，可含 .5）。渲染时乘 SS 变成精细格位移，
        # 于是图层可以"半格"动 —— 灵动感的来源。0 表示不偏移。
        self.sub = (0.0, 0.0)

    def rect(self, x0, y0, x1, y1, ch):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.g[y][x] = ch
        return self

    def dots(self, ch, *pts):
        for x, y in pts:
            self.g[y][x] = ch
        return self

    def ellipse(self, cx, cy, rx, ry, ch='b'):
        for y in range(H):
            for x in range(W):
                if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1.0:
                    self.g[y][x] = ch
        return self

    def shifted(self, dx=0, dy=0):
        """整数格平移。dx/dy 也接受 .5 —— 那会被记进 self.sub，
        由渲染阶段在精细网格上兑现（所以手臂能半格动）。"""
        out = Layer(self.name)
        idx, idy = int(dx), int(dy)          # 整数部分
        fx, fy = dx - idx, dy - idy          # 小数部分（交给精细网格）
        for y in range(H):
            for x in range(W):
                ch = self.g[y][x]
                if ch != '.' and 0 <= x + idx < W and 0 <= y + idy < H:
                    out.g[y + idy][x + idx] = ch
        out.sub = (self.sub[0] + fx, self.sub[1] + fy)
        return out

    def overlay(self, other):
        for y in range(H):
            for x in range(W):
                if other.g[y][x] != '.':
                    self.g[y][x] = other.g[y][x]
        return self


# ------------------------------------------------------------------ 风格参数
# 每个元素都是若干椭圆 (cx, cy, rx, ry)，按顺序叠加
# 小D 的专属形象配置。
# 「baby」：幼态版 —— 弱志选定的「像小火龙」那版坐标
# （头大且与躯干直接相邻、无脖子过渡、吻部略长、大眼 3×4）。
# 曾经被"修"成更规整的比例，反而失去了幼态感，勿再按比例去纠正。
STYLES = {
    'baby': dict(
        label='baby · 幼态版（大头大眼短腿，1.5 头身）',
        torso=[(11.6, 14.0, 5.0, 4.6)],
        head=[(16.5, 6.0, 4.6, 4.4)],
        neck=[],
        snout=[(21.6, 6.6, 2.1, 1.8)],
        # 补一块矩形把吻部前缘在嘴那一行抹平，
        # 否则前缘是阶梯状，嘴会卡在凹口里、前端看起来"下凸"
        snout_rect=(21, 8, 23, 8),
        belly=[(11.6, 15.2, 3.2, 2.9)],
        mouth=(20, 8, 21, 8),
        nose=(22, 6),
        cheeks=[(14, 7), (15, 7), (16, 7)],
        leg_front=dict(c=(9.5, 17.5), r=(2.2, 2.2), toes=[(8, 20), (9, 20), (10, 20)]),
        leg_back=dict(c=(15.0, 17.5), r=(2.2, 2.2), toes=[(14, 20), (15, 20), (16, 20)]),
        tail=[(7.6, 15.0, 2.4, 0.0), (5.2, 13.8, 2.0, 0.4),
              (3.2, 12.0, 1.7, 0.7), (1.4, 10.2, 1.4, 0.9),
              (0.6, 8.4, 1.1, 1.0)],
        # 小爪子：独立前景层。只记「肩」这个固定支点，
        # 具体姿态由 ARM_POSES 里的方向串决定（见下方常量）。
        arm=dict(shoulder=(16, 13)),
        # 拿在手上的道具：始终挂在爪尖右侧一格，绝不脱手（见 make_item）
        item=dict(size=(2, 2)),
        # 面前的笔记本电脑（见 make_laptop）。整体是「亅」形：
        #   竖笔 = 屏幕，立在**右端**（x20~23）
        #   横笔 = 键盘，从屏幕底端向**左**延伸（x14~23）
        # 这个"竖在右、横向左"的摆法就是「亅」—— 别再挪到左边去，
        # 挪过去会变成「L」（踩过这个坑）。
        laptop=dict(base=(16, 15, 23, 17),      # 键盘：x0,y0,x1,y1
                    screen=(21, 9, 22, 15)),    # 屏幕：立在键盘右端
        # 屏幕厚度：亮面 1 格（x21，蓝）+ 厚度 1 格（x22，灰）= 2 格。
        # 侧面看就是薄薄一片；3 格就已经显厚了（踩过两次这个坑）。
        # 键盘左端从 x14 收到 x16：削掉靠身体那一侧，别伸太长。
        # 屏幕高度 y9 是上限：再高到 y8 就顶到吻部/下巴，"会挡脸"。
        # 桌子：放笔记本下方。桌面要横穿身前才有"工作台"的感觉 ——
        # 只在右边一小块会像一块悬空伸出的木板（试过，不好看）。
        # 桌腿给两根（x18、x24）：单腿会显得桌面要塌。
        desk=dict(top=(15, 18, 25), legs=(18, 24)),
        # 漂浮特效的锚点（借鉴 clawd 的 idea / coffee / search）。
        # 位置都**紧贴身体**：灯泡底座接头皮(y1→y2)，咖啡/放大镜贴躯干右侧，
        # 否则 check.py 会报孤立像素。
        fx=dict(idea=(16, 0),      # 灯泡：头顶正上方（头在 x15~18，所以取 16 居中）
                coffee=(17, 13),   # 咖啡杯：贴着躯干右缘（x16）放，中间不留空档
                search=(17, 12)),  # 放大镜：同上，贴手边
        # 屏幕光在小D 身上照亮的位置（右边缘，正对屏幕那一侧）。
        # 都取在**身体已有的像素**上，make_screen_light 还会再确认，
        # 所以不会造出孤立像素。
        light_spots=((16, 13), (16, 14), (15, 13)),
        eyes={
            'normal': dict(rect=(15, 3, 17, 6), hl=[(15, 3)]),
            'half': dict(rect=(15, 5, 17, 6)),
            'closed': dict(rect=(15, 5, 17, 5)),
            'happy': dict(dots=[(14, 6), (15, 5), (16, 5), (17, 5), (18, 6)]),
            'look_r': dict(rect=(15, 3, 17, 6), hl=[(15, 3)], shift=1),
            'look_l': dict(rect=(15, 3, 17, 6), hl=[(15, 3)], shift=-1),
        },
    ),
}


# ------------------------------------------------------------------ 组装图层
def make_body(c):
    b = Layer('body')
    for cx, cy, rx, ry in c['torso']:
        b.ellipse(cx, cy, rx, ry, 'b')
    for cx, cy, rx, ry in c['head']:
        b.ellipse(cx, cy, rx, ry, 'b')
    for cx, cy, rx, ry in c['neck']:
        b.ellipse(cx, cy, rx, ry, 'b')
    for cx, cy, rx, ry in c.get('snout', []):
        b.ellipse(cx, cy, rx, ry, 'b')
    if 'snout_rect' in c:
        b.rect(*c['snout_rect'], 'b')
    for cx, cy, rx, ry in c['belly']:
        b.ellipse(cx, cy, rx, ry, 'l')
    b.rect(*c['mouth'], 'm')
    b.dots('k', c['nose'])
    return b


def make_tail(c, lift=0, root_dy=0):
    """尾巴：沿曲线依次排布的圆，每个圆带权重 t（0=贴身体，1=尾尖）。
    上抬量按 t 线性插值，所以尾巴是整体弯曲、绝不会在某处断开。
    lift 为抬尾幅度。"""
    t = Layer('tail')
    for cx, cy, r, w in c['tail']:
        t.ellipse(cx, cy - lift * w + root_dy, r, r, 'b')
    return t


def make_arm(c, pose='down'):
    """小爪子：独立前景层。
    像素画里手臂不能靠"平移+缩放"来动 —— 那会让它忽长忽短。
    正确做法是**总长恒定、只改关节朝向**：从肩出发固定走 3 步，
    每步方向由 pose 决定（r 右 / l 左 / u 上 / d 下）。
    所以每条姿态的手臂都是 4 个像素长，只是弯折方向不同。"""
    spec = c['arm']
    sx, sy = spec['shoulder']
    dirs = ARM_POSES[pose]

    a = Layer('arm')
    x, y = sx, sy
    pts = [(x, y)]
    for d in dirs:
        dx, dy = _DIRS[d]
        x, y = x + dx, y + dy
        pts.append((x, y))
    for (x, y) in pts:
        if 0 <= x < W and 0 <= y < H:
            a.g[y][x] = 'b'
    # 爪尖用暗色，和身上其它阴影一致
    tx, ty = pts[-1]
    if 0 <= tx < W and 0 <= ty < H:
        a.g[ty][tx] = 'd'
    return a


# 方向 → 单位位移
_DIRS = {'r': (1, 0), 'l': (-1, 0), 'u': (0, -1), 'd': (0, 1)}

# 手臂姿态表：每条 = 从肩出发的 3 步方向串（总长恒为 3，不会忽长忽短）。
# 肩固定在躯干前缘，所以第一步永远是 'r'（先离开身体），
# 后面两步决定"抬 / 垂 / 伸 / 扬"。
ARM_POSES = {
    'down':     'rdr',    # 常态：出去 → 下垂 → 外收（Z 字，默认）
    'droop':    'rdd',    # 无力：一路垂到底（睡觉 / 被拖拽）
    'ready':    'rur',    # 微抬：准备做什么（呼吸 / 走路）
    'up':       'ruu',    # 高举：欢呼 / 挥手最高点
    'wave':     'rru',    # 挥手：手向外扬
    'reach':    'rrd',    # 前伸：够东西 / 托住东西 / 挥手内收
    'type':     'rdd',    # 打字（按下）：爪子落到键盘那一行
    'type_up':  'rru',    # 打字（抬起）：爪子悬在键盘上方 3 格
}


def arm_tip(c, pose='down'):
    """算出手臂指定姿态下爪尖的坐标（道具要靠它定位）"""
    sx, sy = c['arm']['shoulder']
    x, y = sx, sy
    for d in ARM_POSES[pose]:
        dx, dy = _DIRS[d]
        x, y = x + dx, y + dy
    return x, y


def make_item(c, pose='down'):
    """道具：独立前景层，拿在手上时用。
    位置**自动跟随爪尖**（挂在手的右下角），所以无论手臂摆成什么姿态，
    东西都稳稳在手边，绝不会脱手变成悬空像素。"""
    it = Layer('item')
    if 'item' not in c:
        return it
    w, h = c['item']['size']
    tx, ty = arm_tip(c, pose)
    x0, y0 = tx + 1, ty          # 挂在爪尖右侧
    it.rect(x0, y0, x0 + w - 1, y0 + h - 1, 'o')
    return it


def make_leg(c, back=False, lift=0):
    spec = c['leg_back'] if back else c['leg_front']
    cx, cy = spec['c']
    rx, ry = spec['r']
    l = Layer('leg')
    # 大腿：向上伸进躯干内部（会被 body 盖住），
    # 保证身体上下浮动 / 抬腿时腿根不会和身体脱节露缝。
    l.ellipse(cx, cy - ry, rx * 0.85, ry * 2.0, 'b')
    l.ellipse(cx, cy, rx, ry, 'b')
    for x, y in spec['toes']:
        l.g[y][x] = 'd'
    return l.shifted(0, -lift)


def make_desk(c):
    """桌子：一块桌面 + 桌腿，放在笔记本下方，让小D 有个"工作台"。

    只占右下角（笔记本脚下），不横穿小D 的身体 ——
    画布只有 26×22，横穿会把脚和腿的视觉割断。
    桌腿只留右边一根：左边那根会被小D 身体挡住，画了也看不见。
    """
    d = Layer('desk')
    spec = c.get('desk')
    if not spec:
        return d
    x0, y0, x1 = spec['top']
    legs = spec.get('legs', ())
    for x in range(x0, x1 + 1):
        d.g[y0][x] = 'T'                    # 桌面（木色）
    for x in range(x0, x1 + 1):
        if y0 + 1 < H:
            d.g[y0 + 1][x] = 't'            # 桌面下沿 = 厚度
    for lx in legs:                          # 桌腿
        for y in range(y0 + 2, H):
            if 0 <= lx < W:
                d.g[y][lx] = 't'
    return d


def make_laptop(c, keys=('a', 'b')):
    """面前的笔记本电脑：独立前景层，整体是「亅」形。

        竖笔 = 屏幕，立在**右端**（x20~23）
        横笔 = 键盘，从屏幕底端向**左**延伸（x14~23）
        交汇处 = 转轴（键盘右端接住屏幕底端）

    侧面视角的两个要点（都踩过坑，别再画错）：
      · **薄板**：屏幕左边不留边框 —— 亮面直接顶到左缘，只在右边留 1 格
        当厚度。左侧也包一圈边框就变成"厚平板"，不像侧看的屏幕。
      · **竖笔必须在右端、横向左延伸** —— 这才是「亅」。
        把竖笔挪到左端就变成「L」了。
    """
    lap = Layer('laptop')
    spec = c.get('laptop')
    if not spec:
        return lap
    bx0, by0, bx1, by1 = spec['base']
    sx0, sy0, sx1, sy1 = spec['screen']

    def put(x, y, ch):
        if 0 <= x < W and 0 <= y < H:
            lap.g[y][x] = ch

    # ---- 竖笔：屏幕
    # 先铺机身色：右边那一列留作"厚度"，底部当转轴
    lap.rect(sx0, sy0, sx1, sy1, 'n')
    # 亮面从**最左列**开始铺 —— 左侧不留边框（"薄"的关键）
    for y in range(sy0 + 1, sy1):
        for x in range(sx0, sx1):
            put(x, y, 'S')
    # 顶边一道亮白：屏幕顶沿的反光
    for x in range(sx0, sx1):
        put(x, sy0, 'W')

    # ---- 横笔：键盘（向左延伸的长条）
    lap.rect(bx0, by0, bx1, by1, 'n')
    for x in range(bx0, bx1 + 1):
        put(x, by1, 'd')                   # 底边深色 = 机身厚度
    for x in range(bx0 + 1, bx1, 2):       # 键帽：隔一格一个
        put(x, by0 + 1, 'd')
    for i in range(len(keys)):             # 按下的键：亮色
        xx = bx0 + 2 + (i * 3) % max(1, bx1 - bx0 - 2)
        put(xx, by0 + 1, 'S')
    return lap


def make_light_glow(c, scale=1.0):
    """屏幕柔光：淡蓝色的「>」形光晕。

    用户要的效果 —— 光"不一定用像素体现，怎么像光照怎么来"。
    所以这里不走像素网格：先在**放大后的画布**上用多边形画一个「>」，
    再高斯模糊成柔光，最后叠在成品上。像素画 + 真实柔光，
    比一小撮硬像素点更像"光"。
    """
    from PIL import Image, ImageDraw, ImageFilter
    spec = c.get('laptop')
    if not spec:
        return None
    _, sy0, _, sy1 = spec['screen']
    sx0, sx1 = spec['screen'][0], spec['screen'][2]
    bw, bh = W + 2 * PAD, H + 2 * PAD
    w, h = int(bw * scale), int(bh * scale)

    def X(gx):                                  # 网格 x -> 画布 x
        return (gx + PAD + 0.5) * scale

    def Y(gy):
        return (gy + PAD + 0.5) * scale

    # ---- 光：从**整个屏幕**往四周扩散（不是单侧一条「>」）
    # 做法：把屏幕本身当光源，重模糊后自然向周边散开 —— 上、下、左都有，
    # 但只有"感觉"，所以整体透明度压得很低（若隐若现）。
    cx = (X(sx0) + X(sx1)) / 2
    cy = (Y(sy0) + Y(sy1)) / 2
    half_h = (sy1 - sy0 + 1) * scale / 2
    y_mid = cy

    # ① 主体扩散：屏幕形状重模糊 = 向周边散开的柔光
    body = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(body).rectangle(
        [X(sx0) - scale * 0.4, Y(sy0) - scale * 0.3,
         X(sx1) + scale * 0.4, Y(sy1) + scale * 0.3],
        fill=(128, 196, 255, 92))
    body = body.filter(ImageFilter.GaussianBlur(radius=scale * 2.4))

    # ② 中间的"分岔"：从屏幕中部向左上/左下散出两缕，比整条「>」弱得多
    fork = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fork)
    arm = half_h * 0.75
    tip_x = X(sx0)
    lx = tip_x - (sx1 - sx0 + 1) * 1.5 * scale
    lw = max(1, int(0.55 * scale))
    fd.line([(lx, y_mid - arm), (tip_x, y_mid)], fill=(190, 226, 255, 105),
            width=lw, joint='curve')
    fd.line([(lx, y_mid + arm), (tip_x, y_mid)], fill=(190, 226, 255, 105),
            width=lw, joint='curve')
    fork = fork.filter(ImageFilter.GaussianBlur(radius=scale * 1.1))

    return Image.alpha_composite(body, fork)


def make_screen_light(c, body):
    """（保留）屏幕光落在身体上的像素版。

    现在改用 make_light_glow 的柔光，这个函数不再被调用；
    留着是因为「只提亮身体已有像素」这个约束对小尺寸像素画很有用，
    以后要画别的光效可以直接用。
    """
    lt = Layer('light')
    for x, y in c.get('light_spots', ()):
        if 0 <= x < W and 0 <= y < H and body.g[y][x] != '.':
            lt.g[y][x] = 's'
    return lt


def make_fx(c, kind='idea', step=0):
    """头顶/身侧的漂浮特效层（借鉴 clawd 的 idea / coffee / searching）。

    kind:
      'idea'     —— 头顶一个灯泡 + 碎光点（"有灵感 / 在想"
      'coffee'   —— 手边一杯咖啡 + 冒起的热气（"在忙"
      'search'   —— 放大镜 + 镜片反光（"在查"
    step: 0..N 推进动画相位（热气上升、光点闪）。

    **必须贴着身体画**（和头/手相邻），否则 check.py 会报孤立像素 ——
    这是本项目一贯的硬约束。
    """
    fx = Layer('fx')
    spec = c.get('fx')
    if not spec:
        return fx

    def put(x, y, ch):
        if 0 <= x < W and 0 <= y < H:
            fx.g[y][x] = ch

    if kind == 'idea':
        # 灯泡：2×2 的方块才像灯泡（3×1 看就是一条黄杠，试过不好）。
        # 底边紧贴头皮（下一行就是头），所以天然连通、不会被自检抓。
        bx, by = spec['idea']
        put(bx, by, 'y')
        put(bx + 1, by, 'y')
        put(bx, by + 1, 'y')
        put(bx + 1, by + 1, 'y')
        # 碎光点：明灭，且都贴着灯泡（不贴就是孤立像素）
        if step % 2 == 0:
            put(bx - 1, by, 'y')
        if step % 3 == 0:
            put(bx + 2, by + 1, 'y')

    elif kind == 'coffee':
        # 咖啡杯：放在手边（贴着躯干右侧）
        cx, cy = spec['coffee']
        put(cx, cy, 'c')
        put(cx + 1, cy, 'c')
        put(cx, cy + 1, 'c')
        put(cx + 1, cy + 1, 'c')
        # 热气：往上飘（越高用越浅的色）
        for i in range(1, 3):
            yy = cy - i - (step % 2)
            put(cx, yy, 'g' if i < 2 else 'W')

    elif kind == 'search':
        # 放大镜：贴在手边，镜片 + 反光 + 手柄
        mx, my = spec['search']
        put(mx, my, 'g')
        put(mx + 1, my, 'g')
        put(mx, my + 1, 'g')
        put(mx + 1, my + 1, 'W')           # 镜片反光
        put(mx, my + 2, 't')               # 手柄，往下贴到手
    return fx


def make_cheeks(c):
    ch = Layer('cheeks')
    for x, y in c['cheeks']:
        ch.g[y][x] = 'r'
    return ch


def make_eyes(c, kind='normal'):
    e = Layer('eyes')
    spec = c['eyes'][kind]
    if 'rect' in spec:
        e.rect(*spec['rect'], 'k')
    for x, y in spec.get('hl', []):
        e.g[y][x] = 'W'
    for x, y in spec.get('dots', []):
        e.g[y][x] = 'k'
    if spec.get('shift'):
        # 整个眼睛左右平移（眼珠太小，靠高光移位看不出"看"的方向）
        e = e.shifted(spec['shift'], 0)
    return e


def frame(c, tail_lift=0, leg_lift=0, back_lift=0, body_dy=0, eyes='normal',
          arm_pose='down', item=False, item_pose=None, lean=0, head_dy=0,
          keyboard=False, kbd_keys=('a', 'b'), desk=False, arm_sub=(0.0, 0.0),
          fx=None, fx_step=0):
    """组装一帧的全部图层。
    lean: 整体左右倾斜（拖拽 / 翻倒用）；head_dy: 头额外升降（低头 / 抬头）。
    arm_pose: 手臂姿态名（见 ARM_POSES）；item_pose 缺省跟随 arm_pose，
    所以道具永远贴在爪尖旁边，不会脱手。
    keyboard: 是否画面前的笔记本电脑（打字动作）；kbd_keys 决定哪两个键亮着。
    desk: 是否画桌子（工作台场景）。
    arm_sub: 手臂的**亚格**偏移（单位=设计格，可含 .5）。在精细网格上兑现，
    于是手臂能"半格"动 —— 灵动感的关键；body_dy / lean 同样接受小数。"""
    def sh(layer, dx=0, dy=0):
        return layer.shifted(dx, dy)

    def sub(layer, sx=0.0, sy=0.0):
        """追加亚格偏移（累加）。渲染时乘 SS 变成精细格位移。"""
        layer.sub = (layer.sub[0] + sx, layer.sub[1] + sy)
        return layer

    body = make_body(c)
    layers = [sh(make_tail(c, tail_lift, root_dy=body_dy), lean),
              sh(make_leg(c, False, leg_lift), lean),
              sh(make_leg(c, True, back_lift), lean),
              sh(body, lean, body_dy)]
    if desk or keyboard:
        # 桌子和笔记本都是"家具"，不跟身体浮动 —— 它们不该跟着上下抖
        layers.append(make_desk(c))
    if item:
        # 道具挂在爪尖上，必须和手臂用**同一个**亚格偏移，
        # 否则手臂半格动起来时道具会脱手（和之前"手臂变细导致道具脱手"同一类坑）
        layers.append(sub(sh(make_item(c, item_pose or arm_pose), lean, body_dy),
                          *arm_sub))
    if keyboard:
        layers.append(make_laptop(c, kbd_keys))
    layers += [sub(sh(make_arm(c, arm_pose), lean, body_dy), *arm_sub),
               sh(make_cheeks(c), lean, body_dy + head_dy),
               sh(make_eyes(c, eyes), lean, body_dy + head_dy)]
    if fx:
        # 漂浮特效（灯泡/咖啡/放大镜）。不跟身体浮动 —— 它飘在"空气"里，
        # 身体起伏时特效反而该稳住，对比之下更灵动。
        layers.append(make_fx(c, fx, fx_step))
    return layers


# 全部动作（动作表按这个顺序逐行输出）
ALL_ACTIONS = ['idle', 'blink', 'walk', 'run', 'happy', 'wave', 'sleep',
               'hold', 'eat', 'click', 'drag', 'fall', 'look', 'think',
               'type', 'idea', 'coffee', 'search']


def _wave_curve(n, amp=1.0, lag=0.0, phase=0.0):
    """正弦起伏曲线，返回 n 个**亚格**值（可为 0.25 / 0.5）。

    这是"灵动"的核心：以前每个部件只能在整数格之间跳（0 → -1），
    看着一顿一顿；现在能沿着曲线连续移动（0 → -0.25 → -0.5 → -0.75 → -1），
    在精细网格上兑现，所以丝滑但**边缘依然对齐网格、不糊**。

    lag: 让这个部件比别的滞后（0~1，一个周期的比例）—— 惯性感就来自这里：
        身体先动，手臂半拍后才跟上，才是活物而不是一整块平移。
    """
    out = []
    for i in range(n):
        t = (i / n + phase - lag) % 1.0
        out.append(-amp * (1 - math.cos(2 * math.pi * t)) / 2)
    return out


def frames_for(c, action):
    """每个动作返回 8 帧，用**亚格曲线**生成连续运动。

    灵动感的核心（照 clawd 的思路）：
      · 连续曲线代替整数格跳变 —— 0 → -0.25 → -0.5 → -0.75 → -1，不再是 0 → -1
      · 身体与手臂**错开相位**（手臂滞后约 1/4 周期）= 惯性感
      · 在精细网格上兑现，所以丝滑但**边缘依然对齐网格、不糊**
    """
    N = 8
    # 通用：身体起伏 + 手臂滞后（每帧返回 (body_dy, arm_sub_y)）
    def breath(amp=1.1, arm_amp=1.6, lag=0.18, phase=0.0):
        b = _wave_curve(N, amp, phase=phase)
        a = _wave_curve(N, arm_amp, lag=lag, phase=phase)
        return b, a

    # ---- 基础日常
    if action == 'idle':        # 呼吸：身体起伏，手臂滞后半拍（惯性）
        b, a = breath()
        return [frame(c, body_dy=b[i], arm_pose='ready', arm_sub=(0, a[i]))
                for i in range(N)]
    if action == 'blink':
        b, a = breath()
        # 第 3、4 帧眨眼（一次呼吸里眨一次，比每 2 帧眨一次自然）
        eyes = ['normal'] * N
        eyes[3] = eyes[4] = 'closed'
        return [frame(c, body_dy=b[i], arm_pose='ready', arm_sub=(0, a[i]),
                      eyes=eyes[i]) for i in range(N)]
    if action == 'walk':        # 走：腿交替，手反向摆（对侧律），身体上下颠
        b = _wave_curve(N, 1.0)
        a = _wave_curve(N, 1.8, lag=0.25)
        out = []
        for i in range(N):
            half = i < N // 2
            out.append(frame(c, leg_lift=0 if half else 1,
                             back_lift=1 if half else 0,
                             tail_lift=0 if half else 1,
                             body_dy=b[i],
                             arm_pose='ready' if half else 'droop',
                             arm_sub=(0, a[i])))
        return out
    if action == 'run':         # 跑：摆幅更大，身体起伏更猛
        b = _wave_curve(N, 1.8)
        a = _wave_curve(N, 2.6, lag=0.25)
        out = []
        for i in range(N):
            half = i < N // 2
            out.append(frame(c, leg_lift=0 if half else 2,
                             back_lift=2 if half else 0,
                             tail_lift=1 if half else 3,
                             body_dy=b[i],
                             arm_pose='up' if half else 'reach',
                             arm_sub=(0, a[i])))
        return out
    if action == 'happy':       # 蹦跳：跳起时手甩高、落地蜷一下
        b = _wave_curve(N, 2.4)          # 深起伏 = 蹦
        a = _wave_curve(N, 3.0, lag=0.2)
        out = []
        for i in range(N):
            up = b[i] < -1.0             # 腾空
            out.append(frame(c, tail_lift=1 if up else 0,
                             leg_lift=2 if up else 0,
                             back_lift=2 if up else 0,
                             body_dy=b[i], eyes='happy',
                             arm_pose='up' if up else 'down',
                             arm_sub=(0, a[i])))
        return out
    if action == 'wave':        # 挥手：手臂连续上下扬，身体轻晃
        b = _wave_curve(N, 0.8, phase=0.25)
        a = _wave_curve(N, 2.8, lag=0.0)
        return [frame(c, eyes='happy', arm_pose='wave',
                      arm_sub=(0, a[i]), body_dy=b[i]) for i in range(N)]
    if action == 'sleep':       # 睡觉：极缓慢呼吸，手臂垂到底
        b = _wave_curve(N, 0.6, phase=0.5)
        return [frame(c, body_dy=1 + b[i], arm_pose='droop',
                      arm_sub=(0, b[i] * 0.4), head_dy=1,
                      eyes='closed' if i % 4 else 'half')
                for i in range(N)]

    # ---- 交互
    if action == 'hold':        # 拿东西：手托着，随呼吸轻起伏
        b, a = breath(0.8, 1.2)
        return [frame(c, item=True, arm_pose='reach', arm_sub=(0, a[i]),
                      body_dy=b[i]) for i in range(N)]
    if action == 'eat':         # 吃东西：把东西往嘴边送，再慢慢放下
        b = _wave_curve(N, 1.2, phase=0.5)
        a = _wave_curve(N, 3.2)
        out = []
        for i in range(N):
            near = a[i] < -2.0
            out.append(frame(c, item=True, arm_pose='up' if near else 'reach',
                             arm_sub=(0, a[i]*0.5), eyes='happy' if near else 'normal',
                             body_dy=b[i]))
        return out
    if action == 'click':       # 被点击：受惊弹起 → 缩手 → 再弹 → 放松
        b = [-0.5, -1.6, -2.0, -1.2, 0.0, 0.8, 0.4, 0.0]
        a = [0.0, -1.5, -2.6, -2.2, -0.8, 0.4, 0.6, 0.2]
        eyes = ['happy', 'happy', 'normal', 'half', 'half', 'normal', 'normal', 'normal']
        return [frame(c, body_dy=b[i], arm_pose='up' if a[i] < -2 else 'ready',
                      arm_sub=(0, a[i]), eyes=eyes[i]) for i in range(N)]
    if action == 'drag':        # 被拖拽：手无力垂着，随身体左右悠
        lean = _wave_curve(N, 1.4, phase=0.25)
        return [frame(c, leg_lift=-1, arm_pose='droop', lean=lean[i],
                      arm_sub=(0, 0.5), eyes='half') for i in range(N)]
    if action == 'fall':        # 翻倒：整幅翻转成四脚朝天（渲染时垂直翻）
        a = _wave_curve(N, 2.0, phase=0.25)
        return [frame(c, eyes='half' if i % 3 else 'normal', arm_pose='up',
                      arm_sub=(0, a[i])) for i in range(N)]
    if action == 'look':        # 看四方：头跟着转，手轻抬做"探头"感
        seq = ['normal', 'look_r', 'look_r', 'normal', 'look_l', 'look_l', 'normal', 'normal']
        hd = [0, -1, -1, 0, -1, -1, 0, 0]
        return [frame(c, eyes=seq[i], head_dy=hd[i], arm_pose='ready',
                      arm_sub=(0, -0.5 if hd[i] else 0)) for i in range(N)]

    # ---- 工作场景（桌面 + 笔记本）
    if action == 'think':
        # 坐在电脑前思考：手搭在桌上**不敲**，只有眼睛在动 + 偶尔眨眼，
        # 头轻微左右偏 —— 表现出"在琢磨"的神态。
        seq = ['look_r', 'look_r', 'normal', 'look_l', 'look_l', 'normal', 'half', 'look_r']
        tilt = [0.4, 0.6, 0.2, -0.4, -0.6, -0.2, 0.0, 0.3]
        b = _wave_curve(N, 0.7, phase=0.3)
        return [frame(c, keyboard=True, kbd_keys=('a', 'b'), desk=True,
                      arm_pose='ready', arm_sub=(tilt[i] * 0.5, b[i] * 0.5),
                      eyes=seq[i], head_dy=1 if i % 4 < 2 else 0,
                      body_dy=b[i]) for i in range(N)]
    if action == 'type':
        # 敲键盘：爪子连续起落，按键一左一右地亮，眼睛盯着屏幕
        a = _wave_curve(N, 1.6)          # 手的起落曲线
        b = _wave_curve(N, 0.5, lag=0.2)
        keysets = [('a', 'b'), ('c', 'd'), ('e', 'f'), ('g', 'h'),
                   ('i', 'j'), ('k', 'l'), ('m', 'n'), ('o', 'p')]
        return [frame(c, keyboard=True, kbd_keys=keysets[i], desk=True,
                      arm_pose='type' if a[i] > -0.8 else 'type_up',
                      arm_sub=(0, a[i]), eyes='look_r', head_dy=1,
                      body_dy=b[i]) for i in range(N)]

    # ---- 漂浮特效（借鉴 clawd 的 idea / coffee / searching）
    if action == 'idea':
        # 灵光一现：头顶灯泡一闪一闪，身体轻轻一弹
        b = _wave_curve(N, 1.4, phase=0.25)
        eyes = ['normal', 'happy', 'happy', 'normal', 'happy', 'happy', 'normal', 'normal']
        return [frame(c, fx='idea', fx_step=i, body_dy=b[i], eyes=eyes[i],
                      arm_pose='ready' if i % 2 else 'up',
                      arm_sub=(0, b[i] * 0.6)) for i in range(N)]
    if action == 'coffee':
        # 忙里偷闲喝咖啡：热气袅袅上升，手端起放下
        b = _wave_curve(N, 0.8)
        a = _wave_curve(N, 1.4, lag=0.2)
        return [frame(c, fx='coffee', fx_step=i, body_dy=b[i], eyes='half',
                      arm_pose='reach', arm_sub=(0, a[i])) for i in range(N)]
    if action == 'search':
        # 查资料：举着放大镜左右看
        seq = ['look_r', 'look_r', 'normal', 'look_l', 'look_l', 'normal', 'look_r', 'normal']
        b = _wave_curve(N, 0.6)
        return [frame(c, fx='search', fx_step=i, body_dy=b[i], eyes=seq[i],
                      head_dy=-1 if i % 3 == 0 else 0,
                      arm_pose='ready', arm_sub=(-0.4 if i % 4 < 2 else 0.4, b[i] * 0.5))
                for i in range(N)]
    return [frame(c)]


def merge(layers):
    g = [['.'] * W for _ in range(H)]
    for layer in layers:
        for y in range(H):
            for x in range(W):
                if layer.g[y][x] != '.':
                    g[y][x] = layer.g[y][x]
    return [''.join(r) for r in g]


def _raster(grid, palette):
    width, height = W + 2 * PAD, H + 2 * PAD
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    px = img.load()
    for y in range(H):
        for x in range(W):
            col = palette.get(grid[y][x])
            if col:
                px[x + PAD, y + PAD] = col + (255,)
    return img


def _outline_of(img, color=OUTLINE):
    """给已光栅化的图算外轮廓"""
    w, h = img.size
    px = img.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            if px[x, y][3] == 0 and any(
                0 <= x + dx < w and 0 <= y + dy < h and px[x + dx, y + dy][3] > 0
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
            ):
                op[x, y] = color
    return out


def render(grid, palette=COL, scale=SCALE, pad=PAD):
    img = _raster(grid, palette)
    canvas = Image.new('RGBA', img.size, (0, 0, 0, 0))
    canvas.alpha_composite(_outline_of(img))
    canvas.alpha_composite(img)
    return canvas.resize((img.width * scale, img.height * scale), Image.NEAREST)


# 前景层：这些图层要单独再描一圈轮廓，叠在身体之上。
# 否则手臂/道具和身体同色，贴着身体时会被"吞掉"、看不出是独立的小胳膊。
# 这些图层是"独立物体"，会各自描一圈轮廓再叠上去。
# light 不在其中：它是画在身体上的光，不是独立物体，描边会很难看。
# 这些图层是"独立物体"，会各自描一圈轮廓再叠上去。
# light 不在其中：它是画在身体上的光，不是独立物体，描边会很难看。
# desk 在内：桌面要画在身体**前面**（挡住腿的下半截），
# 这样才像"站在工作台后面"，而不是站在桌子旁边。
# fx 在内：漂浮特效（灯泡/咖啡/放大镜）必须在身体**前面**，
# 否则贴着手边的咖啡、放大镜会被身体挡掉（踩过这个坑）。
FRONT_LAYERS = {'arm', 'item', 'laptop', 'desk', 'fx'}

# 前景层（手臂 / 道具）的描边色：深蓝，而不是背景那样的近黑，
# 这样手臂轮廓和身体轮廓能区分开，也不会显得手臂是一团黑边。
FRONT_OUTLINE = (26, 72, 156, 255)


def _outline_inner(fimg, bimg, color):
    """只画手臂「靠身体那一侧」的分界线：
    把分界线画在**身体**像素上（与手臂相邻的那一格），而不是手臂上。
    这样手臂朝外一侧没有描边 —— 手臂本身就是 1 格宽，
    再加一圈描边就会显得又粗又黑。"""
    w, h = fimg.size
    fp = fimg.load()
    bp = bimg.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            if bp[x, y][3] == 0:        # 只看身体像素
                continue
            if any(
                0 <= x + dx < w and 0 <= y + dy < h and fp[x + dx, y + dy][3] > 0
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
            ):
                op[x, y] = color
    return out


def _outline_outer(fimg, color=OUTLINE):
    """给手臂描「朝外那一侧」的轮廓：只描手臂周围的空像素，用近黑，
    和身体外轮廓同色 —— 这样手臂右侧有黑色轮廓，不显得是贴上去的一块。
    靠身体那一侧由 _outline_inner 画在身体像素上，这里不重复处理。"""
    w, h = fimg.size
    fp = fimg.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            if fp[x, y][3] > 0:
                continue                    # 只看空像素
            if any(
                0 <= x + dx < w and 0 <= y + dy < h and fp[x + dx, y + dy][3] > 0
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
            ):
                op[x, y] = color
    return out


def _lip(layers):
    """把若干设计网格图层铺到精细网格上，各自的 .sub 亚格偏移在此兑现。
    每个设计格 -> SS×SS 的实心块，所以外观和旧版一样，只是分辨率更细。"""
    fg = [['.'] * GW for _ in range(GH)]
    for L in layers:
        ox = int(round(L.sub[0] * SS))
        oy = int(round(L.sub[1] * SS))
        for y in range(H):
            row = L.g[y]
            for x in range(W):
                ch = row[x]
                if ch == '.':
                    continue
                for dy in range(SS):
                    fy = y * SS + oy + dy
                    if not (0 <= fy < GH):
                        continue
                    r = fg[fy]
                    for dx in range(SS):
                        fx = x * SS + ox + dx
                        if 0 <= fx < GW:
                            r[fx] = ch
    return [''.join(r) for r in fg]


def _raster_fine(grid, palette):
    img = Image.new('RGBA', (GW + 2 * PADF, GH + 2 * PADF), (0, 0, 0, 0))
    px = img.load()
    for y in range(GH):
        for x in range(GW):
            col = palette.get(grid[y][x])
            if col:
                px[x + PADF, y + PADF] = col + (255,)
    return img


def _outline_thick(img, color=OUTLINE):
    """精细网格上的外轮廓。厚度做成 SS 个精细格 = 1 个设计格，
    和旧版描边一样厚（不然放大后线条会显得太细）。"""
    w, h = img.size
    px = img.load()
    edge = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ep = edge.load()
    for y in range(h):
        for x in range(w):
            if px[x, y][3] == 0 and any(
                0 <= x + dx < w and 0 <= y + dy < h and px[x + dx, y + dy][3] > 0
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
            ):
                ep[x, y] = color
    for _ in range(SS - 1):                 # 向外膨胀到 SS 厚
        nxt = edge.copy()
        np_ = nxt.load()
        for y in range(h):
            for x in range(w):
                if ep[x, y][3] > 0:
                    continue
                if any(0 <= x + dx < w and 0 <= y + dy < h
                       and ep[x + dx, y + dy][3] > 0
                       for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                    np_[x, y] = color
        edge = nxt
        ep = edge.load()
    return edge


def _ring(fimg, bimg, color):
    """前景层与身体的分界线：画在**身体**像素上（与前景相邻的那些格）。
    精细网格版 —— 逻辑同 _outline_inner，只是分辨率更细。"""
    w, h = fimg.size
    fp = fimg.load()
    bp = bimg.load()
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            if bp[x, y][3] == 0:
                continue
            if any(0 <= x + dx < w and 0 <= y + dy < h
                   and fp[x + dx, y + dy][3] > 0
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1)):
                op[x, y] = color
    return out


def _out_size(scale=SCALE):
    """精细网格的最终输出尺寸。按设计格换算，所以和旧版输出**一模一样**。"""
    return ((W + 2 * PAD) * scale, (H + 2 * PAD) * scale)


def render_layers(layers, palette=COL, scale=SCALE, pad=PAD,
                  front_outline=FRONT_OUTLINE, front_outline_mode='outer'):
    """分层渲染：背景层整体描边，前景层（手臂 / 道具）单独描边，
    所以手臂贴着身体时也有轮廓线分隔。

    **在精细网格上渲染**：每个设计格铺成 SS×SS 子格，图层各自的 .sub
    亚格偏移在这里兑现 —— 于是手臂能半格动（灵动感）。描边按 SS 加厚，
    所以视觉厚度和旧版一致；输出尺寸也保持一致。
    """
    bl = [l for l in layers if l.name not in FRONT_LAYERS]
    fl = [l for l in layers if l.name in FRONT_LAYERS]
    bimg = _raster_fine(_lip(bl), palette)
    fimg = _raster_fine(_lip(fl), palette)

    canvas = Image.new('RGBA', bimg.size, (0, 0, 0, 0))
    canvas.alpha_composite(_outline_thick(bimg))
    canvas.alpha_composite(bimg)
    if front_outline_mode == 'inner':
        canvas.alpha_composite(_ring(fimg, bimg, front_outline))
    elif front_outline_mode == 'outer':
        canvas.alpha_composite(_outline_thick(fimg, OUTLINE))
        canvas.alpha_composite(_ring(fimg, bimg, front_outline))
    else:
        canvas.alpha_composite(_outline_thick(fimg, front_outline))
    canvas.alpha_composite(fimg)
    return canvas.resize(_out_size(scale), Image.NEAREST)


def on_bg(img, bg):
    base = Image.new('RGBA', img.size, bg)
    base.alpha_composite(img)
    return base.convert('RGB')


def save_img(img, path, retries=6, delay=0.4, **kw):
    """保存图片并重试。
    本机杀软会实时扫描刚写出的文件，偶尔导致 save() 抛
    OSError [Errno 22] Invalid argument（文件被短暂占用）。
    这里退避重试，避免整个渲染流程因此中断。"""
    import time
    last = None
    for i in range(retries):
        try:
            img.save(path, **kw)
            return path
        except OSError as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


# 需要整幅翻转的动作：摔倒 = 四脚朝天，比左右平移直观得多
FLIP_V_ACTIONS = {'fall'}


def render_action(c, action, layers, scale=SCALE):
    """渲染某个动作的一帧。fall 这类动作在渲染后整幅垂直翻转，
    所以看起来是"摔倒"而不是"左右平移"。

    打字动作额外叠一层屏幕柔光（淡蓝「>」形）——
    像素画 + 真实柔光，比几个硬像素点更像"光"。
    """
    img = render_layers(layers, scale=scale)
    if action in FLIP_V_ACTIONS:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if action == 'type':
        glow = make_light_glow(c, scale=scale)
        if glow is not None and glow.size == img.size:
            img = img.copy()
            img.alpha_composite(glow)
    return img


def main():
    outdir = os.path.join(HERE, 'out')
    os.makedirs(outdir, exist_ok=True)

    chosen = CHAR
    c = STYLES[chosen]

    def paint(layers, scale=SCALE):
        return render_layers(layers, scale=scale)

    # ---- 定妆照：深底 / 浅底各一张
    still = paint(frame(c))
    save_img(still, os.path.join(outdir, 'xiaod.png'))
    for tag, bg in [('dark', (13, 19, 30, 255)),
                    ('light', (238, 241, 246, 255))]:
        save_img(on_bg(still, bg), os.path.join(outdir, f'xiaod_{tag}.png'))

    # ---- 全部动作表 + 循环 GIF
    rows = ALL_ACTIONS
    cols = 4
    cw, chh2 = still.size
    aw = Image.new('RGB', (cw * cols, chh2 * len(rows)), (16, 23, 36))
    for r, action in enumerate(rows):
        fr = frames_for(c, action)
        for col in range(cols):
            aw.paste(on_bg(render_action(c, action, fr[col % len(fr)]),
                           (16, 23, 36, 255)),
                     (col * cw, r * chh2))
    save_img(aw, os.path.join(outdir, 'xiaod_actions.png'))

    durations = {'idle': 220, 'blink': 200, 'walk': 130, 'run': 90,
                 'happy': 120, 'wave': 140, 'sleep': 260, 'hold': 200,
                 'eat': 160, 'click': 120, 'drag': 160, 'fall': 180,
                 'look': 240, 'think': 300}
    for action in ALL_ACTIONS:
        fr = [on_bg(render_action(c, action, g), (13, 19, 30, 255))
              for g in frames_for(c, action)]
        save_img(fr[0], os.path.join(outdir, f'xiaod_{action}.gif'),
                 save_all=True,
                 append_images=fr[1:], duration=durations.get(action, 160),
                 loop=0, optimize=False)

    print('character:', chosen)
    print('actions:', ', '.join(ALL_ACTIONS))
    print('done -> out/')

    # ---- 动作展示图（带中文标签，方便一眼看全）
    make_showcase(os.path.join(outdir, 'xiaod_showcase.png'))


SHOWCASE = [('idle 待机', 'idle'), ('walk 走', 'walk'), ('run 跑', 'run'),
            ('type 敲键盘', 'type'),
            ('think 坐电脑前思考', 'think'), ('idea 灵光一现', 'idea'),
            ('coffee 喝咖啡', 'coffee'), ('search 查资料', 'search'),
            ('hold 拿东西', 'hold'), ('eat 吃东西', 'eat'),
            ('wave 挥手', 'wave'), ('click 被点击', 'click'),
            ('drag 被拖拽', 'drag'), ('fall 翻倒', 'fall'),
            ('look 看四方', 'look'), ('sleep 睡觉', 'sleep')]


def make_showcase(path, scale=26, cols=4):
    from PIL import ImageDraw, ImageFont

    c = STYLES[CHAR]
    rows = []
    for label, action in SHOWCASE:
        fr = frames_for(c, action)
        row = [on_bg(render_action(c, action, g, scale=scale), (13, 19, 30, 255))
               for g in fr[:cols]]
        while len(row) < cols:          # 帧数不足的补齐，保持网格整齐
            row.append(row[-1])
        rows.append((label, row))

    cw = max(im.width for _, row in rows for im in row)
    ch = max(im.height for _, row in rows for im in row)
    try:
        font = ImageFont.truetype(r'C:\Windows\Fonts\msyhbd.ttc', 26)
    except Exception:
        font = ImageFont.load_default()

    label_w, pad = 200, 16
    img = Image.new('RGB', (label_w + cw * cols + pad * 2,
                            ch * len(rows) + pad * 2), (15, 21, 32))
    d = ImageDraw.Draw(img)
    for i, (label, row) in enumerate(rows):
        y = pad + i * ch
        d.text((pad, y + ch // 2 - 16), label, font=font, fill=(214, 226, 240))
        for j, im in enumerate(row):
            img.paste(im, (label_w + pad + j * cw, y))
    save_img(img, path)
    return path


if __name__ == '__main__':
    main()
