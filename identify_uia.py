"""UIA 版扫雷棋盘读取：直接用 Windows UI Automation 读元件，零识别误差。

和 identify.py（像素识别）的关系
--------------------------------
两者**接口一致**：`read_board()` 返回 16x30 的字符矩阵（'#'未开 / 'F'旗 / '.'空白 / '1'-'8'数字），
可以直接互换着用。

但数据来源完全不同：
  - identify.py：截图 + 颜色规则，0.3s，约 2.5% 误判（旗子被读成数字等）
  - 本模块：读系统可访问性树，**100% 准确**，但首次枚举要 ~8.5s

扫雷每个格子都是一个 UIA ButtonControl，Name 里写着全部语义：
    '行 5，列 9 方块(清除)。周围有  2 个地雷。'   ← 已开，数字 2
    '行 5，列 15 方块(隐藏并标记)。'              ← 已插旗
    '行 16，列 1 方块(隐藏)。'                    ← 未开
    '行 5，列 11 方块(清除)。周围无地雷。'         ← 数字 0

速度模型（2026-09-26 实测）
--------------------------
    首次 FindAllBuildCache 全盘枚举     8525 ms   （只需一次）
    持有引用后读 480 格缓存属性              4 ms   （快照）
    持有引用后读单格 CurrentName         ~15 ms/格
    读"边界未开格"（约 30~50 个）    0.45~0.75 s  （每步）

关键洞察：**已开格的状态永不变化**（开了就是开了，数字也不会变）。
所以首次把 480 格的引用和状态都存下来，之后每步只需重读
"参与推理的格子"——即已开数字格的未开邻格（边界格）。
"""
from __future__ import annotations

import re
import sys
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

import comtypes.client as cc
import win32gui
from comtypes import POINTER, cast

try:
    from comtypes.gen import UIAutomationClient as UIA
except ImportError:                       # 首次运行会生成类型库包装（几秒）
    cc.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA

WINDOW_TITLE = '扫雷'
ROWS, COLS = 16, 30

# '行 5，列 9 方块(清除)。周围有  2 个地雷。'
# '行 5，列 15 方块(隐藏并标记)。'
# '行 16，列 1 方块(隐藏)。'
# '行 5，列 11 方块(清除)。周围无地雷。'
CELL_RE = re.compile(
    r'行\s*(\d+)[，,]\s*列\s*(\d+)\s*方块\s*[（(]([^）)]*)[）)]'
    r'(?:\s*[。.]\s*周围\s*(?:有\s*(\d+)\s*个|无)\s*地雷)?'
)

Cell = Tuple[int, int]


def parse_cell_name(name: str) -> Optional[Tuple[int, int, str]]:
    """把元素 Name 解析成 (row, col, 字符)。行列从 1 开始（UIA 里是 1-based）。"""
    if not name:
        return None
    m = CELL_RE.match(name)
    if not m:
        return None
    r, c, state, num = m.group(1), m.group(2), m.group(3), m.group(4)
    state = (state or '').strip()
    r, c = int(r) - 1, int(c) - 1
    if state in ('隐藏',):
        return r, c, '#'
    if '标记' in state:
        return r, c, 'F'
    if state in ('清除', '已清除', '清除。'):
        if num is not None:
            n = int(num)
            return r, c, ('.' if n == 0 else str(n))
        if '无地雷' in name:
            return r, c, '.'
        return r, c, '?'
    return r, c, '?'


class UiaBoard:
    """扫雷棋盘的可访问性树接口。

    用法::

        b = UiaBoard()          # 首次枚举 ~8.5s
        g = b.grid()            # 完整棋盘（首次枚举时的快照）
        b.refresh_border(g)     # 只重读边界格（~0.5s），把 g 更新到最新
        x, y = b.click_point(r, c)   # 格子中心坐标
        b.invoke(r, c)               # 直接用 InvokePattern 点击（不碰鼠标）
    """

    def __init__(self, hwnd: Optional[int] = None, verbose: bool = False):
        self.uia = cc.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        self.verbose = verbose
        self.elements: Dict[Cell, object] = {}
        self.state: Dict[Cell, str] = {}
        # ⚠️ 临时 COM 对象的「墓地」。**不要删这个列表**：实测把这些临时对象
        # （枚举时的 root/cache/cond/found、invoke 时的 pattern 指针）留给垃圾回收，
        # comtypes 会在下一次 GC 的 __del__ → Release 里 access violation，
        # 整个进程直接挂掉（4/4 稳定复现，栈顶是 Garbage-collecting）。
        # 每局只枚举一次，留着的内存代价可忽略。
        self._keepalive: list = []
        self.hwnd = hwnd or win32gui.FindWindow(None, WINDOW_TITLE)
        if not self.hwnd:
            raise RuntimeError(f'找不到窗口 {WINDOW_TITLE!r}')
        self.enumerate_all()

    # ---------------- 枚举 ----------------
    def _build_cache(self):
        c = self.uia.CreateCacheRequest()
        c.AddProperty(UIA.UIA_NamePropertyId)
        c.AddProperty(UIA.UIA_BoundingRectanglePropertyId)
        try:
            c.AddPattern(UIA.UIA_InvokePatternId)
        except Exception:
            pass
        return c

    def enumerate_all(self) -> float:
        """全盘枚举（重开新局后也需要重新调用）。返回耗时秒数。"""
        t0 = time.time()
        root = self.uia.ElementFromHandle(self.hwnd)
        cache = self._build_cache()
        cond = self.uia.CreatePropertyCondition(
            UIA.UIA_ControlTypePropertyId, UIA.UIA_ButtonControlTypeId)
        found = root.FindAllBuildCache(UIA.TreeScope_Descendants, cond, cache)
        # 留着强引用，别让 GC 去 Release 它们（见 __init__ 里 _keepalive 的说明）
        self._keepalive.extend((root, cache, cond, found))
        # 旧的元素引用同理：直接丢掉会让 GC 在随机时刻 Release 它们
        self._keepalive.extend(self.elements.values())
        self.elements.clear()
        self.state.clear()
        for i in range(found.Length):
            e = found.GetElement(i)
            try:
                parsed = parse_cell_name(e.CachedName)
            except Exception:
                continue
            if not parsed:
                continue
            r, c, ch = parsed
            if 0 <= r < ROWS and 0 <= c < COLS:
                self.elements[(r, c)] = e
                self.state[(r, c)] = ch
        self.enum_seconds = time.time() - t0
        if self.verbose:
            print(f'[UIA] 枚举 {len(self.elements)} 格, {self.enum_seconds*1000:.0f} ms')
        return self.enum_seconds

    # ---------------- 读取 ----------------
    def read(self, cells: Sequence[Cell]) -> Dict[Cell, str]:
        """读指定格子的**实时**状态（每格 ~15 ms）。"""
        out = {}
        for (r, c) in cells:
            e = self.elements.get((r, c))
            if e is None:
                continue
            try:
                name = e.CurrentName
            except Exception:
                continue
            parsed = parse_cell_name(name)
            if parsed:
                out[(r, c)] = parsed[2]
        return out

    def border_cells(self, grid: Optional[List[List[str]]] = None) -> List[Cell]:
        """参与推理的格子：已开数字格的未开邻格。

        已开格状态永不变化，所以只有这些才需要每步重读。
        """
        g = grid if grid is not None else self.grid()
        todo: Set[Cell] = set()
        for r in range(ROWS):
            for c in range(COLS):
                v = g[r][c]
                if not v.isdigit():
                    continue
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        rr, cc = r + dr, c + dc
                        if 0 <= rr < ROWS and 0 <= cc < COLS and g[rr][cc] == '#':
                            todo.add((rr, cc))
        return sorted(todo)

    def refresh(self, cells: Optional[Sequence[Cell]] = None,
                grid: Optional[List[List[str]]] = None,
                use_border: bool = True) -> float:
        """重读并更新内部状态。默认只读边界格。返回耗时秒数。"""
        t0 = time.time()
        if cells is None:
            cells = self.border_cells(grid) if use_border else list(self.elements)
        updated = self.read(cells)
        self.state.update(updated)
        return time.time() - t0

    def grid(self, rows: int = ROWS, cols: int = COLS) -> List[List[str]]:
        """当前已知状态转成 16x30 字符矩阵（与 identify.read_board 同格式）。"""
        return [['#' if (r, c) not in self.state else self.state[(r, c)]
                 for c in range(cols)] for r in range(rows)]

    # ---------------- 坐标与操作 ----------------
    def click_point(self, r: int, c: int) -> Tuple[int, int]:
        """格子中心的屏幕坐标（从缓存里的 BoundingRectangle 算）。"""
        e = self.elements[(r, c)]
        rect = e.CachedBoundingRectangle
        return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2

    def cell_rects(self) -> Dict[Cell, Tuple[int, int, int, int]]:
        """每个格子的屏幕矩形 (left, top, right, bottom)，物理像素。

        给像素通道**反标定**几何用：硬编码那套 X0/Y0/CW/CH 是按 669x440 的窗口
        量的，而本机实际格子 39.4px、公式给出 39.19px —— 0.5% 的缩放误差在 30 列
        上累积成 6.3px 漂移，足以把数字笔画挤出采样块。
        """
        out: Dict[Cell, Tuple[int, int, int, int]] = {}
        for (r, c), e in self.elements.items():
            try:
                rc = e.CachedBoundingRectangle
            except Exception:
                continue
            out[(r, c)] = (rc.left, rc.top, rc.right, rc.bottom)
        return out

    def invoke(self, r: int, c: int, pattern: str = 'invoke') -> bool:
        """直接调用元件操作（**不移动真实鼠标、不依赖窗口焦点**）。

        pattern='invoke'  → InvokePattern（等价左键单击）
        pattern='legacy'  → LegacyIAccessiblePattern.DoDefaultAction（MSAA 通道）

        坑：GetCurrentPattern() 返回的是**裸 IUnknown 指针**，没有 .Invoke 方法，
        必须先 cast 成 IUIAutomationInvokePattern 才能调。
        右键插旗没有对应的 UIA 模式，只能走坐标点击。
        """
        e = self.elements.get((r, c))
        if e is None:
            return False
        try:
            if pattern == 'invoke':
                p = e.GetCurrentPattern(UIA.UIA_InvokePatternId)
                self._keepalive.append(p)      # 别留给 GC，否则 __del__→Release 会崩
                cast(p, POINTER(UIA.IUIAutomationInvokePattern)).Invoke()
                return True
            if pattern == 'legacy':
                p = e.GetCurrentPattern(UIA.UIA_LegacyIAccessiblePatternId)
                self._keepalive.append(p)
                cast(p, POINTER(UIA.IUIAutomationLegacyIAccessiblePattern)).DoDefaultAction()
                return True
        except Exception:
            return False
        return False


def read_board(hwnd: Optional[int] = None) -> List[List[str]]:
    """接口兼容函数：一次性枚举并返回完整棋盘（每调用一次要 8.5s，测试用）。"""
    b = UiaBoard(hwnd)
    return b.grid()


if __name__ == '__main__':
    b = UiaBoard(verbose=True)
    g = b.grid()
    print('\n=== UIA 读到的棋盘 ===')
    print('    ' + ''.join(str(c // 10) if c % 10 == 0 else ' ' for c in range(COLS)))
    print('    ' + ''.join(str(c % 10) for c in range(COLS)))
    for r in range(ROWS):
        print(f'{r:2d}  ' + ''.join(g[r]))
    from collections import Counter
    print('\n统计:', dict(Counter(x for row in g for x in row)))

    # 边界格数量与重读耗时
    border = b.border_cells(g)
    print(f'\n边界格（需每步重读）: {len(border)} 个')
    t = b.refresh(border, g)
    print(f'重读边界格耗时: {t*1000:.0f} ms')

    # 与像素版对比
    if '--compare' in sys.argv:
        print('\n=== 与像素识别对比 ===')
        from identify import read_board as pixel_read
        pg = pixel_read()
        diff = [(r, c, pg[r][c], g[r][c]) for r in range(ROWS) for c in range(COLS)
                if pg[r][c] != g[r][c]]
        print(f'不一致 {len(diff)} 处（UIA 为准）:')
        for r, c, px, ua in diff[:20]:
            print(f'   (r{r},c{c})  像素={px!r}  UIA={ua!r}')
