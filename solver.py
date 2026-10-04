"""扫雷求解器：确定性规则 + 子集差分 + 按连通分量的概率枚举。

设计要点
--------
1. 确定性规则（最常用，覆盖大部分步数）：
   - 剩余需放雷数 == 未开邻格数  → 全是雷
   - 剩余需放雷数 == 0           → 全是安全格
   其中「剩余需放雷数」= 数字 − 该格周围已插旗数。

2. 子集差分规则（把人眼常用的「1-2 夹逼」形式化）：
   若约束 A ⊂ B，则 B−A 的雷数 = need(B) − need(A)；
   该差值等于 |B−A| 则 B−A 全是雷，等于 0 则全是安全格。

3. 概率枚举：把「与已开数字相邻的未开格」（边界格）按约束连通分量分组，
   每组独立 DFS 枚举所有满足约束的布雷方案并剪枝，统计每格在多少方案里是雷。
   同组内格子概率 = 计数/总方案数；不同分量相互独立，各自归一。
   非边界格（不与任何数字相邻）用剩余雷数的期望密度估计。

独立性说明：不同分量的解空间是笛卡尔积，所以「每格概率」可以逐分量独立算，
不需要做全局组合爆炸。这是扫雷概率求解的标准做法。
"""
from __future__ import annotations

import itertools
from typing import Dict, Iterable, List, Sequence, Set, Tuple

Cell = Tuple[int, int]
Grid = Sequence[str]


def neighbors(r: int, c: int, rows: int, cols: int) -> List[Cell]:
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols:
                out.append((rr, cc))
    return out


def collect_constraints(grid: Grid, rows: int, cols: int, strict: bool = False):
    """每个数字格 → (未开邻格集合, 剩余需放雷数)。

    strict=False（默认）时丢弃不自洽的约束（例如 need > 未开邻格数）。
    这类条基本都来自识别错（把红底格读成了数字），留着会把整个盘面推错；
    丢掉的代价只是少推一步，比推错安全得多。
    返回 constraints: List[(frozenset[Cell], int)]，只保留还有未开邻格的约束。
    """
    cons = []
    for r in range(rows):
        for c in range(cols):
            v = grid[r][c]
            if not v.isdigit() or int(v) == 0:
                continue
            unk, flags = set(), 0
            for p in neighbors(r, c, rows, cols):
                ch = grid[p[0]][p[1]]
                if ch == '#':
                    unk.add(p)
                elif ch == 'F':
                    flags += 1
            if unk:
                need = int(v) - flags
                if need < 0 or need > len(unk):
                    if strict:
                        raise ValueError(f'(r{r},c{c})={v} 需 {need} 雷但只有 {len(unk)} 个未开邻格')
                    continue
                cons.append((frozenset(unk), need))
    return cons


def certain_moves(constraints, fixed_mines: Set[Cell] | None = None):
    """反复应用规则直到不动点，返回 (必雷集合, 必安全集合)。"""
    mines: Set[Cell] = set(fixed_mines or ())
    safes: Set[Cell] = set()
    changed = True
    while changed:
        changed = False
        # 先把已知的雷/安全格从约束里扣掉，同时调整「还需放几个雷」
        # 注意：必须先在原始集合上数已知雷数，再扣格；先扣再数会永远数到 0。
        active = []
        for orig_cells, orig_need in constraints:
            known_mines = sum(1 for c in orig_cells if c in mines)
            remaining = frozenset(c for c in orig_cells if c not in mines and c not in safes)
            if not remaining:
                continue
            need = orig_need - known_mines
            if need < 0 or need > len(remaining):
                # 与已知信息冲突，说明前面某步插旗/推断有误
                raise ValueError(f'约束冲突：{sorted(remaining)} 需要 {need} 个雷')
            active.append((remaining, need))

        for cells, need in active:
            if need == len(cells):
                for c in cells:
                    if c not in mines:
                        mines.add(c); changed = True
            elif need == 0:
                for c in cells:
                    if c not in safes:
                        safes.add(c); changed = True

        # 子集差分：A ⊂ B
        for i, (A, na) in enumerate(active):
            if A <= mines or A <= safes:
                continue
            for B, nb in active[i + 1:]:
                if A < B:
                    diff, nd = B - A, nb - na
                elif B < A:
                    diff, nd = A - B, na - nb
                else:
                    continue
                if nd < 0 or nd > len(diff):
                    continue
                if nd == len(diff):
                    for c in diff:
                        if c not in mines:
                            mines.add(c); changed = True
                elif nd == 0:
                    for c in diff:
                        if c not in safes:
                            safes.add(c); changed = True
        clash = mines & safes
        if clash:
            raise ValueError(f'同一格既被判雷又被判安全：{sorted(clash)[:5]}')
    return mines, safes


def _components(cells: Set[Cell], constraints) -> List[Tuple[Set[Cell], List[Tuple[frozenset, int]]]]:
    """按约束把边界格分成互不相关的连通分量。"""
    parent: Dict[Cell, Cell] = {c: c for c in cells}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for cs, _ in constraints:
        cs = [c for c in cs if c in cells]
        for a, b in zip(cs, cs[1:]):
            union(a, b)

    groups: Dict[Cell, Set[Cell]] = {}
    for c in cells:
        groups.setdefault(find(c), set()).add(c)

    out = []
    for members in groups.values():
        rel = [(frozenset(c for c in cs if c in members), need)
               for cs, need in constraints if any(c in members for c in cs)]
        out.append((members, rel))
    return out


def _enumerate_component(members: Set[Cell], cons: List[Tuple[frozenset, int]],
                         cap: int = 2_000_000):
    """DFS 枚举一个连通分量。返回 (每格是雷的方案数 dict, 方案总数)。

    剪枝：按「约束剩余需求 / 剩余可放数」在赋值过程中检查可行性。
    """
    cells = sorted(members)
    idx = {c: i for i, c in enumerate(cells)}
    n = len(cells)
    masks = []
    for cs, need in cons:
        m = 0
        for c in cs:
            m |= 1 << idx[c]
        masks.append((m, need))

    counts = [0] * n
    total = 0
    if n > 40:                      # 极端情况，交给近似处理
        return {c: 0.5 for c in cells}, 0

    # 变量顺序：优先出现在更多约束里的格子（先约束后自由）
    order = sorted(range(n), key=lambda i: -sum(1 for m, _ in masks if m >> i & 1))
    pos_of = [0] * n
    for k, i in enumerate(order):
        pos_of[i] = k

    # 约束在「赋值顺序」下的位掩码，便于剪枝
    ordered_masks = []
    for m, need in masks:
        mm = 0
        for i in range(n):
            if m >> i & 1:
                mm |= 1 << pos_of[i]
        ordered_masks.append((mm, need))

    def feasible(done: int, ones: int) -> bool:
        for mm, need in ordered_masks:
            placed = bin(ones & mm).count('1')
            remaining = bin(mm & ~done).count('1')
            if placed > need or placed + remaining < need:
                return False
        return True

    stack = [(0, 0, 0)]
    while stack:
        k, done, ones = stack.pop()
        if not feasible(done, ones):
            continue
        if k == n:
            total += 1
            if total > cap:
                return {}, 0
            for i in range(n):
                if ones >> pos_of[i] & 1:
                    counts[i] += 1
            continue
        i = order[k]
        # 先试「不是雷」，再试「是雷」（顺序无关，只是让搜索树稍浅）
        stack.append((k + 1, done | (1 << k), ones))
        stack.append((k + 1, done | (1 << k), ones | (1 << k)))
    return {cells[i]: (counts[i] / total if total else 0.0) for i in range(n)}, total


def probabilities(grid: Grid, rows: int, cols: int, remaining_mines: int | None = None):
    """返回 (概率表 dict[Cell]=p_mine, 诊断信息 dict)。

    确定格直接给 0 或 1；边界格按分量枚举；非边界格用剩余密度估计。
    """
    cons = collect_constraints(grid, rows, cols)
    mines, safes = certain_moves(cons)
    probs: Dict[Cell, float] = {c: 1.0 for c in mines}
    probs.update({c: 0.0 for c in safes})

    unknown = {(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == '#'}
    border = set()
    for cs, _ in cons:
        border |= set(cs)
    border -= mines | safes
    inner = unknown - border - mines - safes

    # 边界格：按分量独立枚举
    live_cons = []
    for cs, need in cons:
        cs2 = frozenset(c for c in cs if c not in mines and c not in safes)
        if cs2:
            live_cons.append((cs2, need - sum(1 for c in cs if c in mines)))

    comps = _components(border, live_cons)
    total_solutions = 1
    comp_solutions = []
    for members, rel in comps:
        pm, tot = _enumerate_component(members, rel)
        total_solutions *= max(tot, 1)
        comp_solutions.append((members, pm, tot))
        probs.update(pm)

    # 非边界格：用剩余雷数的期望密度
    if inner:
        expected_border_mines = 0.0
        for members, pm, tot in comp_solutions:
            if tot:
                expected_border_mines += tot * sum(pm.values()) / max(len(comp_solutions), 1)
        # 简化：用各分量的平均雷数（权重为方案数）
        weighted = 0.0
        for members, pm, tot in comp_solutions:
            if tot:
                weighted += sum(pm.values())
        if remaining_mines is not None:
            left = max(remaining_mines - len(mines) - weighted, 0.0)
            dens = min(left / len(inner), 1.0)
        else:
            dens = 0.5
        for c in inner:
            probs[c] = dens

    diag = {
        'constraints': len(cons),
        'certain_mines': len(mines),
        'certain_safes': len(safes),
        'border': len(border),
        'inner': len(inner),
        'components': [(len(m), tot) for m, _, tot in comp_solutions],
    }
    return probs, diag


def decide(grid: Grid, rows: int, cols: int, remaining_mines: int | None = None):
    """返回一步决策 dict(action in {'flag','open'}, cell, prob, reason)。"""
    cons = collect_constraints(grid, rows, cols)
    mines, safes = certain_moves(cons)
    if safes:
        return {'action': 'open', 'cell': sorted(safes)[0], 'prob': 0.0,
                'reason': f'规则推出的安全格（共 {len(safes)} 个）'}
    if mines:
        return {'action': 'flag', 'cell': sorted(mines)[0], 'prob': 1.0,
                'reason': f'规则推出的雷（共 {len(mines)} 个）'}
    probs, diag = probabilities(grid, rows, cols, remaining_mines)
    if not probs:
        return {'action': 'none', 'cell': None, 'prob': None, 'reason': '没有可推断的格子'}
    best = min(probs.items(), key=lambda kv: kv[1])
    return {'action': 'open', 'cell': best[0], 'prob': best[1],
            'reason': f'无确定步，选雷概率最低者（{len(probs)} 个候选）', 'diag': diag}


if __name__ == '__main__':
    from identify import grab_window, read_board, show, ROWS, COLS

    grid = read_board()
    show(grid)
    cons = collect_constraints(grid, ROWS, COLS)
    mines, safes = certain_moves(cons)
    print(f'约束 {len(cons)} 条 → 必雷 {len(mines)} 个，必安全 {len(safes)} 个')
    print('  必雷  :', sorted(mines)[:12])
    print('  必安全:', sorted(safes)[:12])
    probs, diag = probabilities(grid, ROWS, COLS)
    print('\n诊断:', diag)
    if probs:
        top = sorted(probs.items(), key=lambda kv: kv[1])[:8]
        print('雷概率最低的 8 格:')
        for c, p in top:
            print(f'  {c}  p={p:.4f}')
    print('\n决策:', {k: v for k, v in decide(grid, ROWS, COLS).items() if k != "diag"})
