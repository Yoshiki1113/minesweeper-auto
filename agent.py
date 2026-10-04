"""扫雷执行器：识别 → solver 决策 → 真实点击。

安全设计（控制真实鼠标，必须保守）
---------------------------------
1. --dry（默认）：只识别 + 决策 + 打印将要点的屏幕坐标，一个键都不按。
2. 急停（两个通道，任一命中立即退出）：① 按下 Win 键（左/右均可）；
   ② 把鼠标移到屏幕左上角 (0,0)–(6,6) 区域。
3. 坐标校验：每次点击前确认目标落在扫雷窗口 rect 内，否则拒绝执行。
4. 单步限速：一次只动一格，动作后等界面刷新再重新识别，不批量连点。
5. 步数上限 + 全程日志：每步写 log/step-*.jsonl，出事能回看。
6. 无确定步时默认停手（不猜）；只有显式 --guess 才允许用概率枚举赌。

用法:
    python agent.py --dry            # 只算不点（先跑这个）
    python agent.py --once 5         # 实点 5 步
    python agent.py                  # 一直点到没有确定步为止
    python agent.py --guess          # 允许在无确定步时赌概率最低的格子
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time

import win32api
import win32con
import win32gui
import win32process

import paths

# 开发时脚本同目录可能有 _MEIPASS 解不开的模块；打包后 _MEIPASS 自己就在 sys.path 里。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from identify import COLS, ROWS, cell_center, grab_window, read_board, show
from solver import collect_constraints, certain_moves, probabilities

WINDOW_TITLE = '扫雷'
LOG_DIR = paths.data('log')          # 开发时=脚本目录\log；打包后=exe 旁边\log
os.makedirs(LOG_DIR, exist_ok=True)
STOP_ZONE = 6          # 左上角 6x6 像素 = 急停区
# 急停按键：VK_LWIN / VK_RWIN。用 GetAsyncKeyState 轮询物理键状态，
# 不注册全局热键、不抢占系统 Win 键本身的功能。
STOP_KEYS = ((0x5B, 'Win 键'), (0x5C, '右 Win 键'))
# 同一个格子连续插旗失败这么多次就停手。坐标错位/窗口被遮挡时，没有熔断会无限重试。
FLAG_FAIL_LIMIT = 3
OPEN_DELAY = 0.32      # 点开后等界面刷新（扫雷有展开动画）
FLAG_DELAY = 0.20


def ensure_ready(hwnd: int) -> bool:
    """确保窗口可接收鼠标输入（UIA 的 Invoke 不依赖焦点，只有插旗/chord 需要）。

    **返回是否确认已在前台。** 为什么要回读校验：SetForegroundWindow 会被
    Windows 前台锁定**静默拒绝**（不抛异常、返回 None），不校验的话就表现为
    「右键点了没反应、旗子永远插不上」，而且会一直重试。
    实测后台投递（PostMessage WM_RBUTTONDOWN/UP）经典扫雷不认，所以只能走真实鼠标，
    也就必须真的把它置前。
    """
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.35)
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    # 第一次：常规尝试
    try:
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.06)
    except Exception:
        pass
    if win32gui.GetForegroundWindow() == hwnd:
        return True
    # 第二次：借用当前前台线程的输入队列，绕过前台锁定
    try:
        fg = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(fg)[0]
        my_thread = win32api.GetCurrentThreadId()
        attached = False
        if fg_thread and fg_thread != my_thread:
            attached = bool(ctypes.windll.user32.AttachThreadInput(fg_thread, my_thread, True))
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                ctypes.windll.user32.AttachThreadInput(fg_thread, my_thread, False)
        time.sleep(0.08)
    except Exception:
        pass
    return win32gui.GetForegroundWindow() == hwnd


def click(x: int, y: int, right: bool = False, both: bool = False) -> None:
    """扫雷用点击。

    both=True → 左键+右键同时按（chord）：在数字格上做，把它周围已确认安全的格子一次全开。
    延迟经实测压到最小值（原来 both 合计 0.19s、单键 0.14s，是全局第二大开销）。
    """
    _t0 = time.perf_counter()
    try:
        win32api.SetCursorPos((x, y))
        time.sleep(0.012)
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, 0, 0, 0)   # 触发 WM_MOUSEMOVE
        time.sleep(0.008)
        if both:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.008)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)          # 两键同时按住的时间，太短游戏认不出 chord
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.008)
            win32api.mouse_event(win32con.MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            return
        down, up = ((win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP) if right
                    else (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP))
        win32api.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.04)              # 按下到抬起，太短会漏点
        win32api.mouse_event(up, 0, 0, 0, 0)
    finally:
        _add('click(动作+内等待)', time.perf_counter() - _t0)


# 三个会阻塞主窗口的模态对话框（实测）
#   '游戏失败' → 退出(X) / 重新开始这个游戏(R) / **再玩一局(P)**
#   '新游戏'   → 退出并开始新游戏(N) / 重新开始这个游戏(R) / 继续游戏(K)
#   '游戏胜利' → **再玩一局(P)** / 退出(X)
# 注意：胜/败两个框都只需按 **P** 就能直接开新局（实测），
# 不要再走去菜单「新游戏」那条路（会弹确认框，麻烦且易卡）。
MODAL_TITLES = (('游戏失败', 'gameover'), ('新游戏', 'newgame'), ('游戏胜利', 'gamewin'))

_ENTER = {'gameover': ord('P'),     # 再玩一局（直接开新局，实测可用）
          'newgame': ord('K'),      # 继续游戏（保住进度）
          'gamewin': ord('P')}      # 再玩一局


def find_modal() -> Tuple[int, str]:
    """找阻塞主窗口的模态对话框。返回 (hwnd, kind)，没有则 (0, '')。"""
    for title, kind in MODAL_TITLES:
        h = win32gui.FindWindow(None, title)
        if h and win32gui.IsWindowVisible(h):
            return h, kind
    return 0, ''


def handle_modal(prefer_continue: bool = True) -> str:
    """清掉模态框，返回形如 'gameover→R' 的说明（无框返回 ''）。

    「游戏失败」→ 按 R（重新开始这个游戏）
    「新游戏」  → 优先按 K（**继续游戏，保住当前进度**）；
                  prefer_continue=False 时才按 N（退出并开始新游戏）
    """
    h, kind = find_modal()
    if not h:
        return ''
    try:
        win32gui.SetForegroundWindow(h)
    except Exception:
        pass
    time.sleep(0.25)
    key = _ENTER[kind]
    if kind == 'newgame' and not prefer_continue:
        key = ord('N')                     # 明确要求开新局时才选 N
    key_tap(key)
    time.sleep(1.2)
    if kind in ('gameover', 'gamewin'):
        # P 直接开新局：棋盘全变（且元素引用可能指向旧局对象），
        # 必须清掉增量缓存，让下一次 read_grid 全量重读。
        _TODO['cells'] = None
    return f'{kind}→{chr(key)}'


def game_over_dialog() -> int:
    """只看「游戏失败」弹窗（失败的唯一可靠信号），返回句柄，0 = 没有。"""
    h = win32gui.FindWindow(None, '游戏失败')
    return h if (h and win32gui.IsWindowVisible(h)) else 0


def dismiss_game_over() -> bool:
    """关掉失败弹窗并重开：按 R（重新开始这个游戏）。"""
    if not game_over_dialog():
        return False
    print('      ' + handle_modal())
    return True


def stop_reason():
    """急停检测。命中返回原因字符串，未命中返回 None。

    两个通道，任一命中即停：
      1) 按下 Win 键（左/右都认）。**这是主通道** —— 代理自己在动鼠标，
         原来的鼠标通道在代理点击期间会被它自己占用，用户根本抢不到。
      2) 鼠标移到屏幕左上角 STOP_ZONE x STOP_ZONE 区域（原有通道，保留）。

    用 GetAsyncKeyState 轮询物理键状态：不注册全局热键（注册 Win 组合键会
    抢掉系统自身的 Win 功能），也不依赖窗口焦点（UIA 点击时焦点不在本进程）。
    """
    for vk, name in STOP_KEYS:
        if win32api.GetAsyncKeyState(vk) & 0x8000:
            return f'按下了 {name}'
    x, y = win32api.GetCursorPos()
    if x < STOP_ZONE and y < STOP_ZONE:
        return f'鼠标移到左上角 {STOP_ZONE}x{STOP_ZONE}'
    return None


def stopped() -> bool:
    return stop_reason() is not None


def log_step(step: int, payload: dict) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    day = time.strftime('%Y%m%d')
    with open(os.path.join(LOG_DIR, f'step-{day}.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({'t': time.strftime('%H:%M:%S'), 'step': step, **payload},
                           ensure_ascii=False) + '\n')


def wait_stable(timeout: float = 4.0, interval: float = 0.22):
    """等棋盘稳定：连续两次识别结果完全一致才返回。

    扫雷展开有动画，动画没画完就截图会把“正在展开”读成“没生效”，
    进而误判点击失败、甚至误判游戏结束。
    """
    prev = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        g = read_board()
        sig = ''.join(''.join(row) for row in g)
        if sig == prev:
            return g
        prev = sig
        time.sleep(interval)
    return read_board()


def find_chordable(grid, exclude=None, anchor=None):
    """找一个能 chord（左键+右键同时按）的已开数字格：周围雷已标全且还有未开格。

    在数字格上双键 → 把它周围剩下的未开格一次全开。
    anchor: 上次操作的位置。优先选靠近锚点的（鼠标少跑路，形成贪吃蛇式连续推进）；
            同等条件下选能一次开最多格的。
    返回 (cell, n_to_open) 或 None。
    """
    from solver import neighbors
    exclude = exclude or set()
    best = None
    for r in range(ROWS):
        for c in range(COLS):
            if (r, c) in exclude:
                continue
            v = grid[r][c]
            if not v.isdigit() or int(v) == 0:
                continue
            nbr = neighbors(r, c, ROWS, COLS)
            flags = sum(1 for p in nbr if grid[p[0]][p[1]] == 'F')
            unk = [p for p in nbr if grid[p[0]][p[1]] == '#']
            if flags == int(v) and unk:
                score = len(unk) * 10
                if anchor is not None:
                    score -= abs(r - anchor[0]) + abs(c - anchor[1])
                if best is None or score > best[2]:
                    best = ((r, c), len(unk), score)
    return (best[0], best[1]) if best else None


def find_chordable_by_digit(grid, exclude=None, anchor=None):
    """找可 chord 的已开数字格，**数字越小越优先**。

    为什么数字小优先（用户经验）：数字 1 只要确定 1 个雷就能把周围一片抳开，
    数字 2 要确定 2 个，依次递推 —— 先拿 1/2 开刀，展开效率最高。
    同数字时选**离锚点最近**的（鼠标少跑路，就地推进）；再同则选能开最多格的。
    返回 ((r,c), 可开格数) 或 None。
    """
    from solver import neighbors
    exclude = exclude or set()
    best = None
    for r in range(ROWS):
        for c in range(COLS):
            if (r, c) in exclude:
                continue
            v = grid[r][c]
            if not v.isdigit() or int(v) == 0:
                continue
            nbr = neighbors(r, c, ROWS, COLS)
            flags = sum(1 for p in nbr if grid[p[0]][p[1]] == 'F')
            unk = [p for p in nbr if grid[p[0]][p[1]] == '#']
            if flags == int(v) and unk:
                d = abs(r - anchor[0]) + abs(c - anchor[1]) if anchor else 0
                key = (int(v), d, -len(unk))   # 数字小 > 离得近 > 开得多
                if best is None or key < best[0]:
                    best = (key, (r, c), len(unk))
    return (best[1], best[2]) if best else None


def local_decide(grid, anchor=None, skip_open=None, allow_guess=False,
                 remaining=None, no_chord=None, disable_chord=False):
    _t0 = time.perf_counter()
    try:
        return _local_decide_inner(grid, anchor, skip_open, allow_guess,
                                   remaining, no_chord, disable_chord)
    finally:
        _add('decide', time.perf_counter() - _t0)


def _local_decide_inner(grid, anchor, skip_open, allow_guess, remaining,
                        no_chord, disable_chord):
    """局部化决策：以锚点为中心就地推进，数字小的格子优先。

    用户的贪吃蛇式思路：
      能 chord 先 chord（一次开一片）→ 不能就标雷（标完下一轮就能 chord）
      → 再不能就开确定安全格 → 都推不动了才猜。
    锚点会跟着新开的区域走，所以鼠标不会满屏幕乱飞。
    返回 (action, cell, reason)。
    """
    # 0) 开局首点：全新棋盘（一格未开）没有任何数字可推理，而扫雷保证首点必定安全
    #    （布雷是第一次点击之后才做的），所以直接点中心格。
    #    ⚠️ 少了这一段，空盘会一路落到下面第 4 条「无确定步」直接停手 ——
    #    表现就是「不加 --guess 连开局都开不了」，跟 README 的用法对不上。
    if sum(row.count('#') for row in grid) == ROWS * COLS:
        return 'open', (ROWS // 2, COLS // 2), '开局首点（必定安全）'

    cons = collect_constraints(grid, ROWS, COLS)
    mines, safes = certain_moves(cons)

    def near(cells):
        if not anchor:
            return sorted(cells)[0]
        return min(cells, key=lambda p: abs(p[0]-anchor[0]) + abs(p[1]-anchor[1]))

    # 1) 能 chord 就先 chord（数字小的优先）—— 一次开一片，最快
    if not disable_chord:
        ch = find_chordable_by_digit(grid, exclude=no_chord, anchor=anchor)
        if ch:
            cell, k = ch
            return 'chord', cell, f'chord (r{cell[0]},c{cell[1]}) 数字{grid[cell[0]][cell[1]]} 可开 {k} 格'

    # 2) 能推必雷就标雷（标完下一轮才能 chord）
    if mines:
        m = near(mines)
        return 'flag', m, f'确定是雷（共 {len(mines)} 个），标最近的'

    # 3) 确定安全格
    if safes:
        cand = [c for c in safes if c not in (skip_open or set())]
        if cand:
            s = near(cand)
            return 'open', s, f'确定安全格（共 {len(cand)} 个）'

    # 4) 真推不动了
    if not allow_guess:
        return None, None, '无确定步（且未允许猜）'
    probs, _ = probabilities(grid, ROWS, COLS, remaining)
    cands = [c for c in probs if grid[c[0]][c[1]] == '#']
    if not cands:
        return None, None, '没有可推断的格子'
    cell = min(cands, key=lambda c: probs[c])
    return 'open', cell, f'无确定步，赌概率最低 {probs[cell]:.3f}'


def key_tap(vk: int, delay: float = 0.08) -> None:
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(delay)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def hard_restart() -> int:
    """彻底重开：杀掉 Minesweeper.exe 重新启动，返回新窗口句柄（失败返回 0）。

    菜单「新游戏(N)」有时不生效（特别在本局已经结束后），把进程杀掉重起最可靠。
    """
    import subprocess
    exe = paths.minesweeper_exe()
    subprocess.run(['taskkill', '/F', '/IM', 'Minesweeper.exe'], capture_output=True)
    time.sleep(1.8)
    if not os.path.exists(exe):
        print('      找不到 Minesweeper.exe')
        return 0
    subprocess.Popen([exe], cwd=os.path.dirname(exe))
    for _ in range(14):
        time.sleep(0.6)
        h = win32gui.FindWindow(None, WINDOW_TITLE)
        if h:
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            time.sleep(0.8)
            # 新进程 → 旧的 UIA 元素引用全部失效，必须重新枚举，否则后面读到的
            # 全是旧对象的快照（表现为“点开无效”“未开格数不变”）。
            init_reader(h, use_uia=(_READER['uia'] is not None))
            return h
    return 0


def restart_game(hwnd: int) -> int:
    """重开一局。返回可用窗口句柄（0 = 失败）。

    关键：失败/胜利弹窗在 play_game 里**已经按过 P**（按完弹窗就消失、游戏开始重置），
    所以这里不能上来就找弹窗——多半找不到，会误判成“没救”而白杀进程。
    正确顺序：先等游戏重置完，看盘面是不是新局（480 个未开格）；
    盘面没重置才回头去找弹窗（stuck 退出、弹窗还挂着的情况）。
    """
    _TODO['cells'] = None
    time.sleep(1.5)
    n = sum(row.count('#') for row in read_grid(force_full=True))
    print(f'      重开后未开格 {n}')
    if n == ROWS * COLS:
        return hwnd
    h, kind = find_modal()
    if kind:
        print('      ' + handle_modal())
        time.sleep(1.8)
        _TODO['cells'] = None
        n = sum(row.count('#') for row in read_grid(force_full=True))
        print(f'      弹窗重开后未开格 {n}')
        if n == ROWS * COLS:
            return hwnd
    print('      仍然没开成新局 → 重启进程')
    _TODO['cells'] = None
    return hard_restart()


# ---------------- UIA 感知层（优先）----------------
# UIA 直接读系统可访问性树，格子的未开/已插旗/数字都是**系统给的**，
# 不像像素识别会把旗子读错（实测误判会导致“只一个雷却标了两个旗”，
# 于是 solver 以为雷已标全，双键 chord 怎么都不动）。
#
# 速度：首次枚举 ~8.6s（每局一次）；之后热态全量重读 ~200ms（比像素识别还快）。
_READER = {'uia': None}
_TODO = {'cells': None}      # 待重读的格子（= 上次的未开格）
_TIMING: dict = {}           # 各环节累计耗时（秒），用于找出真正的瓶颈


def _add(key: str, dt: float) -> None:
    _TIMING[key] = _TIMING.get(key, 0.0) + dt


def report_timing(steps: int) -> None:
    """打印各环节耗时占比（找瓶颈用）。"""
    tot = sum(_TIMING.values())
    if not tot:
        return
    print(f'\n   [计时]（{steps} 步，共 {tot:.1f}s，平均 {tot/max(steps,1)*1000:.0f} ms/步）')
    for k, v in sorted(_TIMING.items(), key=lambda kv: -kv[1]):
        print(f'      {k:14s} {v:8.1f}s  {v/tot*100:5.1f}%   '
              f'({v/max(steps,1)*1000:5.0f} ms/步)')
    _TIMING.clear()


def init_reader(hwnd: int, use_uia: bool = True):
    """初始化棋盘读取器。UIA 失败时自动回退像素识别。"""
    _TODO['cells'] = None
    if not use_uia:
        _READER['uia'] = None
        return None
    try:
        from identify_uia import UiaBoard
        _READER['uia'] = UiaBoard(hwnd, verbose=True)
        print('感知层：UIA（首次全量枚举一次；之后只增量重读未开格）')
    except Exception as exc:
        print(f'UIA 初始化失败（{type(exc).__name__}: {exc}）→ 回退像素识别')
        _READER['uia'] = None
    return _READER['uia']


def read_grid(force_full: bool = False, focus=None, radius: int = 2,
              auto_expand: bool = False):
    """读当前棋盘。

    UIA 路径走增量，三种模式：
      force_full=True  → 全量 480 格（重开/校验用，~7s）
      focus=(r,c)      → **只读它周围 (2*radius+1)² 的格子**（半径1=9格 140ms，半径2=25格 312ms）
      都不给            → 只读“边界格”（与已开数字格相邻的未开格，~30~60 个 = 0.5~0.9s）
    依据：已开格的状态永远不会变（开了就是开了，数字也不再变），不必重读。

    auto_expand：配合 radius=1 用。扫雷里**只有空白格 '.' 会连锁展开**
    （数字格展开不会超出自己那一圈），所以先读 3x3，一旦看到 '.' 就补读
    外面一圈（5x5）——既快又不会漏掉一大片连锁展开。
    """
    u = _READER['uia']
    if u is None:
        _t0 = time.perf_counter()
        try:
            return read_board()
        finally:
            _add('read(像素)', time.perf_counter() - _t0)
    _t0 = time.perf_counter()
    try:
        if force_full:
            todo = list(u.elements)
            if todo:
                u.refresh(todo)
        elif focus is not None:
            r0, c0 = focus

            def _ring(rr):
                return [(r, c)
                        for r in range(max(0, r0 - rr), min(ROWS, r0 + rr + 1))
                        for c in range(max(0, c0 - rr), min(COLS, c0 + rr + 1))]

            inner = _ring(radius)
            if inner:
                u.refresh(inner)
            if auto_expand and radius < 2:
                g1 = u.grid()
                if any(g1[r][c] == '.' for r, c in inner):
                    seen = set(inner)
                    extra = [c for c in _ring(2) if c not in seen]
                    if extra:
                        u.refresh(extra)
        else:
            todo = _TODO['cells'] or list(u.elements)
            try:
                bset = set(u.border_cells(u.grid()))
                narrowed = [c for c in todo if c in bset]
                if narrowed:
                    todo = narrowed
            except Exception:
                pass
            if todo:
                u.refresh(todo)
        g = u.grid()
        _TODO['cells'] = [(r, c) for r in range(ROWS) for c in range(COLS) if g[r][c] == '#']
        return g
    except Exception as exc:
        print(f'      ⚠ UIA 读取失败（{type(exc).__name__}: {exc}）→ 本轮回退像素识别')
        return read_board()
    finally:
        _add('read(UIA)', time.perf_counter() - _t0)


def read_cells(cells):
    """只读指定的几个格子（~2ms/格，用于点完后的局部校验）。返回 grid。

    注意：必须用 **refresh()**——它会把读到的值写回内部盘面；
    read() 只是“查一下返回给你”，盘面不变。用错会变成无限补标同一个雷。
    """
    u = _READER['uia']
    if u is None:
        return read_grid()
    try:
        u.refresh(list(cells))
    except Exception:
        pass
    return u.grid()


def click_point_for(r: int, c: int, rect):
    """格子中心的屏幕坐标 —— **优先用 UIA 的 BoundingRectangle**。

    为什么不能用 identify.cell_center()：那套 X0/Y0/CW/CH 是按 100% 缩放标定的
    （标定窗口 669x440 = 逻辑尺寸），而本进程设了 PER_MONITOR_DPI_AWARE，
    GetWindowRect 返回的是**物理像素**。两者混用，在高 DPI 下会把坐标算偏约 2 倍
    —— 本机 4K@200% 实测：想点 (8,15) 实际点到 (2,6)，表现就是「旗子永远插不上」，
    而开格走 UIA Invoke 不用坐标，所以看起来只有插旗/chord 坏。
    UIA 的 BoundingRectangle 是物理像素、跟 DPI 无关、每台机器都对。
    """
    u = _READER['uia']
    if u is not None:
        try:
            return u.click_point(r, c)
        except Exception:
            pass
    return cell_center(r, c, rect)      # 回退：像素识别通道（identify 已按 DPI 缩放）


def open_cell(r: int, c: int, rect) -> str:
    """开一格。UIA 优先用 InvokePattern（**不碰鼠标、不依赖焦点**），否则坐标点击。"""
    u = _READER['uia']
    if u is not None and u.invoke(r, c):
        return 'invoke'
    x, y = cell_center(r, c, rect)
    click(x, y)
    return 'mouse'


def flag_cell(r: int, c: int, rect) -> bool:
    """插旗（UIA 没右键通道，只能鼠标），插完用 UIA 验证一下。"""
    x, y = click_point_for(r, c, rect)
    click(x, y, right=True)
    u = _READER['uia']
    if u is not None:
        try:
            u.refresh([(r, c)])          # refresh 会写回盘面，read 只是查询
        except Exception:
            pass
        return u.grid()[r][c] == 'F'
    return True


def play_game(hwnd, dry, allow_guess, max_steps, remaining, game_no=1):
    """打完一局。返回 ('win'|'lost'|'stuck', 步数)。"""
    done = 0
    failed: set = set()          # open 点了没反应的格子
    failed_chord: set = set()    # 双击失败的数字格
    open_fail = 0                # 连续 open 失效（真正的游戏结束信号）
    chord_fail = 0               # 连续 chord 失效
    flag_fail = 0                # 连续插旗失效
    flag_fail_count: dict = {}   # 格子 -> 连续插旗失败次数（熔断用）
    disable_chord = False        # 双键 chord 实测有效（之前失败是选错了“雷没标全”的格子）
    anchor = None                # 锚点：上次 chord 的位置（贪吃蛇式就地推进）
    grid = None                  # 缓存的棋盘：上一步末尾更新，避免开头重复读
    for step in range(1, max_steps + 1):
        # 0) 先清模态框：它阻塞主窗口，会表现为“怎么点都不动”
        handled = handle_modal(prefer_continue=True)
        if handled.startswith('gamewin'):
            print(f'🎉 检测到「游戏胜利」弹窗（{handled}）')
            log_step(step, {'action': 'gamewin', 'handled': handled})
            return 'win', done
        if handled.startswith('gameover'):
            print(f'★ 检测到「游戏失败」弹窗（{handled}）→ 本局判负')
            log_step(step, {'action': 'gameover-dialog', 'handled': handled})
            # 先把失败盘面存下来（重开后雷的位置就看不到了），供事后分析
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                snap = read_grid()
                fn = os.path.join(LOG_DIR, f'failed-{time.strftime("%Y%m%d-%H%M%S")}.txt')
                with open(fn, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(''.join(r) for r in snap))
                print(f'      失败盘面已存: {fn}')
            except Exception as _e:
                print(f'      盘面快照失败: {_e}')
            return 'lost', done
        if handled:
            print(f'[{step}] 处理模态框：{handled}（保住当前局）')
            continue
        why = stop_reason()
        if why:
            print(f'★ 检测到急停（{why}），退出')
            log_step(step, {'action': 'estop', 'reason': why})
            return 'stuck', done
        ensure_ready(hwnd)
        if grid is None:
            grid = read_grid()               # 只在首次读；之后靠上一步末尾的增量更新
        rect = win32gui.GetWindowRect(hwnd)
        n_unopened = sum(row.count('#') for row in grid)
        if n_unopened == 0:
            print(f'🎉 通关！剩余未开格 0')
            return 'win', done

        # 提速关键：标雷不改变棋盘布局，所以一次识别就可以把该标的雷连着标完，
        # 不用每标一个都重新截图 + 等稳定（原来是每步一次识别，极慢）。
        if not dry:
            try:
                _cons = collect_constraints(grid, ROWS, COLS)
                _mines, _safes = certain_moves(_cons)
            except ValueError as _exc:
                open_fail += 1
                print(f'[{step}] 盘面矛盾（{_exc}），重试 {open_fail}/5')
                log_step(step, {'action': 'conflict', 'error': str(_exc)})
                if open_fail >= 5:
                    print('★ 连续矛盾，盘面不可读')
                    log_step(step, {'action': 'conflict-giveup'})
                    return 'stuck', done      # 交给外层重开（输只认弹窗）
                time.sleep(0.5)
                continue
            if _mines:
                # 插旗走真实鼠标 → 必须确认窗口真的在最前面（SetForegroundWindow 会静默失败）
                if not ensure_ready(hwnd):
                    print(f'[{step}] ⚠ 扫雷窗口未能置前（插旗走真实鼠标，可能失效）')
                todo = sorted(_mines)
                print(f'[{step}] 批量标雷 {len(todo)} 个：{todo[:6]}{" ..." if len(todo) > 6 else ""}')
                for m in todo:
                    why = stop_reason()
                    if why:
                        print(f'★ 检测到急停（{why}），退出')
                        log_step(step, {'action': 'estop', 'reason': why})
                        return 'stuck', done
                    mx, my = click_point_for(*m, rect)
                    if not (rect[0] <= mx <= rect[2] and rect[1] <= my <= rect[3]):
                        continue
                    click(mx, my, right=True)
                    time.sleep(0.06)     # 0.09 会漏点；0.06 实测够用
                    done += 1
                    log_step(done, {'action': 'flag', 'cell': list(m)})
                # 校验：用 UIA 确认旗子真的插上了。
                # 注意右键是 toggle（插旗↔取消）：如果第一次其实成功了、只是 UIA 读得稍慢，
                # 直接补标会把旗**取消**，solver 以为有旗实际没有 → chord 展开就踩雷。
                # 所以补标前先等一拍重读。只读刚插旗的那几个格子（2ms/格），
                # 不要走边界格模式（0.9s）—— 这是每步最大的隐性开销。
                grid = read_cells(todo)
                missed = [m for m in todo if grid[m[0]][m[1]] != 'F']
                if missed:
                    time.sleep(0.25)
                    grid = read_cells(missed)
                    missed = [m for m in missed if grid[m[0]][m[1]] != 'F']
                if missed:
                    print(f'      ⚠ {len(missed)} 个旗子确实没插上：{missed[:5]}（补一次）')
                    for m in missed:
                        mx, my = click_point_for(*m, rect)
                        click(mx, my, right=True)
                        time.sleep(0.06)
                    # 补完再验一次，并给「同一个格子连续失败」计数。
                    # 没有这道熔断，坐标错位时会一直原地重试（实测空转 445 步把这一局点输）。
                    time.sleep(0.25)
                    grid = read_cells(missed)
                    still = [m for m in missed if grid[m[0]][m[1]] != 'F']
                    for m in missed:
                        if m in still:
                            flag_fail_count[m] = flag_fail_count.get(m, 0) + 1
                        else:
                            flag_fail_count.pop(m, None)
                    if still and max(flag_fail_count[m] for m in still) >= FLAG_FAIL_LIMIT:
                        print(f'★ 同一批格子连续 {FLAG_FAIL_LIMIT} 次插旗无效：{still[:3]}，停手')
                        print('  开格走 UIA Invoke 不依赖坐标，所以照常能用；')
                        print('  插旗/chord 依赖真实鼠标坐标 —— 坐标错位或窗口被遮挡时会一直失败。')
                        log_step(step, {'action': 'flag-giveup',
                                        'cells': [list(m) for m in still]})
                        return 'stuck', done
                else:
                    for m in todo:
                        flag_fail_count.pop(m, None)
                continue

        try:
            action, cell, reason = local_decide(grid, anchor=anchor,
                                               skip_open=failed,
                                               allow_guess=allow_guess,
                                               remaining=remaining,
                                               no_chord=failed_chord,
                                               disable_chord=disable_chord)
        except ValueError as exc:
            open_fail += 1
            print(f'[{step}] 盘面矛盾（{exc}），重试 {open_fail}/5')
            log_step(step, {'action': 'conflict', 'error': str(exc)})
            if open_fail >= 5:
                print('★ 连续矛盾，盘面不可读（多半已结束）')
                return 'lost', done
            time.sleep(0.6)
            continue
        if action is None:
            print(f'[{step}] 停手：{reason}')
            log_step(step, {'action': 'stop', 'reason': reason})
            return 'stuck', done
        rr, cc = cell
        x, y = click_point_for(rr, cc, rect)
        if not (rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]):
            print(f'[{step}] ★ 拒绝执行：坐标 ({x},{y}) 不在窗口内')
            log_step(step, {'action': 'refuse', 'cell': cell, 'xy': [x, y]})
            return 'stuck', done
        print(f'[{step}] {action:5s} (r{rr},c{cc}) 未开 {n_unopened}  {reason}')
        log_step(step, {'action': action, 'cell': list(cell), 'xy': [x, y],
                        'unopened': n_unopened, 'reason': reason})
        if dry:
            return 'stuck', 0
        if action == 'open':
            open_cell(rr, cc, rect)          # UIA 走 Invoke（不碰鼠标、不依赖焦点）
        else:
            click(x, y, right=(action == 'flag'), both=(action == 'chord'))
        _t_w = time.perf_counter()
        time.sleep(0.03 if action == 'open' else 0.05)
        _add('等待游戏反应', time.perf_counter() - _t_w)
        # 动作后的读盘：
        #   flag → 插旗只是把 '#' 变成 'F'，不会展开、也不会动别的格子，
        #         本地改一下就好，省一次读盘（~300ms × 每局几十次）。
        #   open/chord → 只读刚操作位置周围 3x3（140ms）；一旦读到 '.'（空白格，
        #         全场唯一会连锁展开的东西）就自动补读外面一圈。
        if action == 'flag':
            grid[rr][cc] = 'F'          # grid() 返回 List[List[str]]，可直接改
        else:
            grid = read_grid(focus=cell, radius=1, auto_expand=True)
        after = sum(row.count('#') for row in grid)
        # 每次动作后也看下弹窗。
        # ⚠️ 胜负都必须在这里判：**胜利框往往正是动作之后弹出来的**（比如最后一步 chord）。
        # 只判负、把 gamewin 当普通模态框处理的话，会按 P 开一局新游戏、再拿着旧盘面
        # 继续打（表现为连串「点开无效」→ 判 stuck）—— 实测就这样白白漏掉过一次胜利。
        post = handle_modal(prefer_continue=True)
        if post.startswith('gamewin'):
            print(f'🎉 检测到「游戏胜利」弹窗（{post}）→ 本局胜利')
            log_step(step, {'action': 'gamewin-dialog', 'after': after})
            return 'win', done
        if post.startswith('gameover'):
            print(f'★ 检测到「游戏失败」弹窗（{post}）')
            log_step(step, {'action': 'gameover-dialog', 'after': after})
            return 'lost', done
        if post:
            print(f'[{step}] 处理模态框：{post}')
        if after == n_unopened and action == 'chord':
            # 左右键同时按未被识别 —— 这不是游戏结束，降级为逐个点开
            failed_chord.add(cell)
            chord_fail += 1
            print(f'      ⚠ 双键 chord 没生效，降级为逐个点开（连败 {chord_fail}）')
            if chord_fail >= 2:
                disable_chord = True
        elif after == n_unopened and action == 'flag':
            # 插旗不改未开格以外的数量，但 '#' → 'F' 会让 '#' 少 1
            flag_fail += 1
            print(f'      ⚠ 插旗没生效（连败 {flag_fail}）')
        elif after == n_unopened and action == 'open':
            failed.add(cell)
            open_fail += 1
            print(f'      ⚠ 点开无效（连败 {open_fail}）')
            if open_fail >= 8:      # 靠弹窗判负，这里只当兵底防止原地打转
                print('★ 多次点开无反应（且无失败弹窗），停手')
                return 'stuck', done
        else:
            open_fail = 0
            if action == 'chord':
                chord_fail = 0
                anchor = cell            # 锚点前移：下一步优先在附近继续开
                if anchor and n_unopened - after >= 3:
                    print(f'      → 锚点更新到 (r{cell[0]},c{cell[1]})，继续就地展开')
        done += 1
    return 'stuck', done


def run(dry: bool, allow_guess: bool, max_steps: int, remaining: int | None, loop: int = 1):
    hwnd = win32gui.FindWindow(None, WINDOW_TITLE)
    print(f'模式: {"DRY（不点击）" if dry else "实点"}  '
          f'赌博: {"允许" if allow_guess else "禁止"}  '
          f'最多 {loop} 局  急停: 按 Win 键 或 鼠标移到左上角 {STOP_ZONE}x{STOP_ZONE}')
    results = {}
    for game in range(1, loop + 1):
        print(f'\n===== 第 {game} 局 =====')
        result, steps = play_game(hwnd, dry, allow_guess, max_steps, remaining, game)
        results[result] = results.get(result, 0) + 1
        print(f'第 {game} 局结束: {result}  步数 {steps}')
        report_timing(steps)
        if dry or result == 'win':
            break
        # 只有确认失败（弹窗）才重开；stuck（比如暂时无确定步）交给外层或继续
        if result != 'lost':
            print(f'本局是 {result}，不是失败 → 按你的要求：不重开')
            break
        if game < loop:
            # 失败/胜利弹窗里按 P 就已经开好新局了（play_game 里已完成），
            # 这里只需确认棋盘确实重置，不要再去点菜单「新游戏」。
            time.sleep(1.0)
            _TODO['cells'] = None
            n_now = sum(row.count('#') for row in read_grid(force_full=True))
            if n_now == ROWS * COLS:
                print(f'      已由弹窗「再玩一局(P)」开好新局（未开 {n_now}）')
                continue
            print(f'      棋盘未重置（未开 {n_now}）→ 重试一次')
            new_hwnd = restart_game(hwnd)
            if not new_hwnd:
                print('重开失败，停止')
                break
            if new_hwnd != hwnd:
                hwnd = new_hwnd
                print(f'      新窗口句柄 {hwnd}')
    print(f'\n汇总: {results}')


def main() -> None:
    """入口（打包后由 main.py 或 --run-agent 调）。"""
    # 控制台编码可能是 GBK，碰到 ⏱ 🎉 这类字符会直接抛 UnicodeEncodeError 把整个
    # 程序干掉（实测打包后必崩）。统一改成 UTF-8 并容错，写日志文件也安全。
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只算不点')
    ap.add_argument('--guess', action='store_true', help='无确定步时允许赌概率最低格')
    ap.add_argument('--once', type=int, default=0, help='单局最多走 N 步（0=不限）')
    ap.add_argument('--loop', type=int, default=1, help='最多打 N 局（输了自动重开），直到赢')
    ap.add_argument('--no-uia', action='store_true', help='不用 UIA，回退像素识别')
    ap.add_argument('--mines-left', type=int, default=None, help='剩余雷数（界面左下角）')
    a = ap.parse_args()
    # 写 PID 文件：GUI / ms.py 靠它找到并停掉这个进程
    pidf = paths.data('run.pid')
    try:
        with open(pidf, 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    try:
        init_reader(win32gui.FindWindow(None, WINDOW_TITLE), use_uia=not a.no_uia)
        run(dry=a.dry, allow_guess=a.guess,
            max_steps=(a.once if a.once > 0 else 500), remaining=a.mines_left,
            loop=a.loop)
    finally:
        try:
            if os.path.exists(pidf):
                os.remove(pidf)
        except Exception:
            pass


if __name__ == '__main__':
    main()
