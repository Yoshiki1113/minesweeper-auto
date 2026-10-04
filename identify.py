"""扫雷棋盘识别：截图 → 30x16 状态矩阵 → 文本化。

标定参数来自 2026-09-26 实测（D:\\tmp\\ms_window.png，窗口 669x440 @ 第二屏）。
这是**备用**的像素识别通道，主力是 identify_uia.py（UIA 语义读取，100% 准确）。

用法:
    python identify.py            # 截当前扫雷窗口，打印 ASCII 棋盘
    python identify.py --json     # 同时写出 D:\\tmp\\ms_board.json
"""
import ctypes
import json
import sys
import time
from collections import Counter

import numpy as np

# ---------- 必须最先执行：设 DPI 感知，否则 125% 缩放下的坐标会错位 ----------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
except Exception:
    pass

import win32con
import win32gui
from PIL import Image, ImageDraw, ImageGrab

WINDOW_TITLE = '扫雷'

# ---------- 标定值（窗口位置变了要重测）----------
X0, Y0 = 41.0, 82.0          # 第 0 行第 0 列格子的左上角（窗口客户区坐标）
CW, CH = 19.667, 19.688      # 格子宽/高（px）
ROWS, COLS = 16, 30          # 高级局

# 未开格：饱和蓝。已开格：白/浅灰。红旗：红色像素占未开格面积的 6% 以上。
# BLUE_MIN_RATIO 保留作参考；实际判定用“blue + red 占比”（见 _analyze）
BLUE_MIN_RATIO = 0.85
FLAG_RED_RATIO = 0.03
SAT_MIN = 55                 # 判数字笔画的最低饱和度
MIN_DIGIT_PIXELS = 6         # 一个数字至少要这么多饱和像素


def grab_window(title: str = WINDOW_TITLE):
    """截取窗口。三个坑：最小化时 rect 为 -25600；必须 all_screens；先设 DPI。"""
    h = win32gui.FindWindow(None, title)
    if not h:
        raise RuntimeError(f'找不到窗口 {title!r}，扫雷启动了吗？')
    win32gui.ShowWindow(h, win32con.SW_RESTORE)
    time.sleep(0.08)
    l, t, r, b = win32gui.GetWindowRect(h)
    if l < -10000:
        raise RuntimeError('窗口仍是最小化状态')
    return ImageGrab.grab(bbox=(l, t, r, b), all_screens=True), (l, t, r, b)


def _digit(blk: np.ndarray):
    """从格子里认数字。数字笔画只有 1~2 个纯色像素（其余是抗锯齿），
    所以取**饱和度最高的那批像素（笔画核心）**再判颜色 + 亮度。

    用平均色会出错：数字 4 的核心是 (0,0,139)（暗蓝）、数字 1 是 (0,0,255)（亮蓝），
    一旦混入抗锯齿的白，两者的平均亮度都拉高，4 就被认成了 1。
    """
    flat = blk.reshape(-1, 3).astype(float)
    sat = flat.max(axis=1) - flat.min(axis=1)
    sel = flat[sat > SAT_MIN]
    if len(sel) < MIN_DIGIT_PIXELS:
        return None
    core_sat = sel.max(axis=1) - sel.min(axis=1)
    keep = max(3, len(sel) // 2)
    core = sel[np.argsort(-core_sat)[:keep]]
    r, g, b = core[:, 0].mean(), core[:, 1].mean(), core[:, 2].mean()
    lum = (r + g + b) / 3
    if r > g and r > b:                      # 红系：3 亮 / 5 暗
        return 3 if lum > 65 else 5
    if g > r and g > b:                      # 绿 = 2
        return 2
    if b > r and b > g:                      # 蓝系：1 亮 / 4 深
        return 1 if lum > 65 else 4
    if g > r and abs(g - b) < 35:            # 青 = 6
        return 6
    return None


def _analyze(blk: np.ndarray) -> str:
    """单格判定：'#' 未开 / 'F' 旗 / '.' 已开空白 / '1'-'8' 数字 / '?' 未知。

    '?' 出现在红底格子（雷区高亮、标红等）——实测这类格子会被错读成
    红色数字 3，而它参与约束会把整个盘面推错（约束冲突），所以直接标未知。
    """
    r, g, b = blk[..., 0], blk[..., 1], blk[..., 2]
    tot = blk.shape[0] * blk.shape[1]
    blue = ((b - r > 35) & (b > 105)).sum() / tot
    red = ((r > 140) & (r > g + 60) & (r > b + 60)).sum() / tot
    # 红底格（雷区标红），不能当数字用
    if red > 0.50 and blue < 0.30:
        return '?'
    # 未开格与旗子：底色（蓝）几乎铺满整格，旗子则是「蓝底 + 一块红旗」。
    # 用 blue+red 而不是单独 blue：旗子的红旗会占掉一部分蓝，单看 blue 会漏。
    # 阈值 0.85 又刚好把“深蓝色笔画粗的数字 4”挡在外面（它 blue≈0.64）。
    if blue + red > 0.85:
        return 'F' if red > FLAG_RED_RATIO else '#'
    d = _digit(blk)
    return str(d) if d is not None else '.'


def read_board(img=None):
    """返回 16x30 的字符矩阵。"""
    if img is None:
        img, _ = grab_window()
    a = np.asarray(img.convert('RGB')).astype(int)
    grid = []
    for rr in range(ROWS):
        row = []
        for cc in range(COLS):
            y1, x1 = int(Y0 + rr * CH), int(X0 + cc * CW)
            blk = a[y1 + 2:y1 + int(CH) - 1, x1 + 2:x1 + int(CW) - 1]
            row.append(_analyze(blk))
        grid.append(row)
    return grid


def cell_center(rr: int, cc: int, win_rect=None):
    """格子 → 屏幕像素坐标（点击用）。"""
    x = X0 + (cc + 0.5) * CW
    y = Y0 + (rr + 0.5) * CH
    if win_rect:
        x += win_rect[0]
        y += win_rect[1]
    return int(round(x)), int(round(y))


def show(grid):
    print('    ' + ''.join(str(c // 10) if c % 10 == 0 else ' ' for c in range(COLS)))
    print('    ' + ''.join(str(c % 10) for c in range(COLS)))
    for rr, row in enumerate(grid):
        print(f'{rr:2d}  ' + ''.join(row))
    print()
    print('统计:', dict(Counter(x for row in grid for x in row)))


if __name__ == '__main__':
    img, rect = grab_window()
    g = read_board(img)
    show(g)
    print('窗口 rect:', rect)
    if '--json' in sys.argv:
        out = r'D:\tmp\ms_board.json'
        json.dump({'grid': [''.join(r) for r in g]}, open(out, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print(f'\n已写出 {out}')
