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
import win32ui
from PIL import Image

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

# 标定时的**窗口宽度**。上面那套 X0/Y0/CW/CH 是在这个尺寸下量出来的，
# 而它等于逻辑尺寸（100% 缩放）。本机 4K@200% 时窗口物理宽度是 1333，
# 比值 ≈ 1.98 —— 不缩放的话算出来的坐标会偏约 2 倍（实测点 (8,15) 点到 (2,6)）。
CAL_WIN_W = 669.0


def _scale_for(win_rect=None, img=None) -> float:
    """当前窗口相对标定尺寸的缩放比（高 DPI 修正）。拿不到窗口信息就返回 1.0。"""
    w = None
    if win_rect:
        w = win_rect[2] - win_rect[0]
    elif img is not None:
        w = img.size[0]
    if not w:
        return 1.0
    s = w / CAL_WIN_W
    # 边框/主题带来的几个像素差异不缩放，避免抖动
    return s if abs(s - 1.0) > 0.05 else 1.0


def calibrate_from_rects(cell_rects, win_rect):
    """用 UIA 给的格子矩形**反标定** X0/Y0/CW/CH，返回 (X0, Y0, CW, CH)。

    为什么需要：上面那套硬编码值是按 669x440 的窗口量的，而本机实际格子是 39.4px、
    公式给出 39.19px —— 0.5% 的缩放误差在 30 列上累积成 6.3px 漂移，足以把数字笔画
    挤出采样块（实测整盘准确率因此掉到 85%）。

    cell_rects: {(r,c): (left, top, right, bottom)}，物理像素
    win_rect:   窗口矩形，物理像素
    标定结果归一到「窗口宽 = CAL_WIN_W」的逻辑坐标，这样 _scale_for 那套仍然适用。
    """
    global X0, Y0, CW, CH
    l, t, r_, b_ = win_rect
    w = r_ - l
    if w <= 0 or len(cell_rects) < 100:
        return X0, Y0, CW, CH
    k = CAL_WIN_W / w                      # 物理 -> 逻辑
    cols, xs, rows, ys = [], [], [], []
    for (rr, cc), (el, et, er, eb) in cell_rects.items():
        cols.append(cc)
        xs.append(((el + er) / 2 - l) * k)
        rows.append(rr)
        ys.append(((et + eb) / 2 - t) * k)
    cw, x_int = np.polyfit(cols, xs, 1)    # 中心 = (X0 + 0.5*CW) + c*CW
    ch, y_int = np.polyfit(rows, ys, 1)
    X0 = float(x_int - 0.5 * cw)
    Y0 = float(y_int - 0.5 * ch)
    CW, CH = float(cw), float(ch)
    return X0, Y0, CW, CH


def grab_window(title: str = WINDOW_TITLE):
    """截取窗口，返回 (PIL.Image, rect)。

    **用 BitBlt 抓窗口自身的 DC，不要用 ImageGrab.grab(bbox=...)。**
    实测（本机 4K@200%，进程设了 PER_MONITOR_DPI_AWARE）：
      - ImageGrab.grab(bbox, all_screens=True) 抓回来的是**完全错误的区域** ——
        对照 UIA 真值 480 格**全错**。这正是「像素识别不准」的真正根源，
        不是阈值标得不好；
      - 而且慢 12 倍：92ms vs BitBlt 的 7.8ms。
    BitBlt 从窗口 DC 取像素，不受遮挡影响，也不经过 DPI 坐标换算。

    另外两个坑：最小化时 GetWindowRect 为 -25600；必须先设 DPI 感知（见文件头）。
    """
    h = win32gui.FindWindow(None, title)
    if not h:
        raise RuntimeError(f'找不到窗口 {title!r}，扫雷启动了吗？')
    # 只有真从最小化恢复时才需要等；窗口正常时这 80ms 是白等（实测它占了
    # grab_window 总耗时的 2/3：125ms -> 15ms）
    if win32gui.IsIconic(h):
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        time.sleep(0.08)
    l, t, r, b = win32gui.GetWindowRect(h)
    if l < -10000:
        raise RuntimeError('窗口仍是最小化状态')
    w, hh = r - l, b - t
    hwndDC = win32gui.GetWindowDC(h)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfcDC, w, hh)
        saveDC.SelectObject(bmp)
        saveDC.BitBlt((0, 0), (w, hh), mfcDC, (0, 0), win32con.SRCCOPY)
        info = bmp.GetInfo()
        # GetBitmapBits 返回的是拷贝，所以下面可以立刻释放 GDI 对象
        img = Image.frombuffer('RGB', (info['bmWidth'], info['bmHeight']),
                               bmp.GetBitmapBits(True), 'raw', 'BGRX', 0, 1)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(h, hwndDC)
    return img, (l, t, r, b)


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
        # 阈值 65 是错的：实测（整局 108 次比对、2619 个 3 的样本）
        #   数字 3 的核心亮度 lum = 61.9 ~ 63.0
        #   数字 5 的核心亮度 lum = 42.5 ~ 43.1
        # 65 把**所有** 3 都判成了 5（曾占全部像素误读的 92%）。取中点 52。
        return 3 if lum > 52 else 5
    # 青 = 6。**必须放在蓝系判断之前**：数字 6 的核心是 (6,124,128)，B 只比 G 高 4，
    # 会被下面的「b > g」分支抢走判成 1（实测 87 个 6 全错）。
    # 数字 1(64,81,190)/4(3,4,131) 的 |G-B| 都 >100，不会误判成 6。
    if g > r and b > r and abs(g - b) < 35:
        return 6
    if g > r and g > b:                      # 绿 = 2
        return 2
    if b > r and b > g:                      # 蓝系：1 亮 / 4 深
        return 1 if lum > 65 else 4
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
    # 旗子：蓝底 + 一块红旗。
    # 判据是「有红 且（蓝底还在 或 红占比小到不可能是数字 3）」——
    # 实测（整局 108 次比对）：
    #   旗子   red 0.117~0.138（很窄），blue 0.065~0.782
    #   数字 3 red 0.210~0.241（**比旗子还红**），blue 0.000~0.340
    #   数字 5 red 0.002~0.045，blue 0.000~0.237
    # 所以 red 单独就能把旗子(≤0.138)和 3(≥0.210)分开，取 0.18 做分界；
    # 少数旗子的 blue 只有 0.065（远低于 0.45），靠 red 这一支兜住。
    if red > 0.05 and (blue > 0.45 or red < 0.18):
        return 'F'
    # 未开格：蓝底几乎铺满（实测 blue 0.856~0.941）。阈值从 0.85 降到 0.75 留余量
    # （数字 1 的 blue 最高才 0.603，不会误判）。
    if blue + red > 0.75:
        return 'F' if red > FLAG_RED_RATIO else '#'
    d = _digit(blk)
    return str(d) if d is not None else '.'


def read_board(img=None):
    """返回 16x30 的字符矩阵。"""
    rect = None
    if img is None:
        img, rect = grab_window()
    s = _scale_for(rect, img)          # 高 DPI 修正：本机 200% 时 s≈2
    pad = max(1, int(round(2 * s)))
    a = np.asarray(img.convert('RGB')).astype(int)
    grid = []
    for rr in range(ROWS):
        row = []
        for cc in range(COLS):
            y1, x1 = int((Y0 + rr * CH) * s), int((X0 + cc * CW) * s)
            blk = a[y1 + pad:y1 + int(CH * s) - 1, x1 + pad:x1 + int(CW * s) - 1]
            row.append(_analyze(blk))
        grid.append(row)
    return grid


def cell_center(rr: int, cc: int, win_rect=None):
    """格子 → 屏幕像素坐标（点击用）。

    ⚠️ 注意：agent 的鼠标路径**优先用 UIA 的 BoundingRectangle**（见 agent.click_point_for），
    这个函数只是没有 UIA 时的回退。win_rect 必须是**和本进程 DPI 感知一致**的物理坐标。
    """
    s = _scale_for(win_rect)
    x = (X0 + (cc + 0.5) * CW) * s
    y = (Y0 + (rr + 0.5) * CH) * s
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
