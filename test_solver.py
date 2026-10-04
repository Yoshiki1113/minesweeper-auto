"""solver 的正确性测试：随机生成真实雷局，检查推断永不出错。

这是「自动玩扫雷」的前提——`certain_moves` 说是雷的必须真是雷，
说安全的必须真安全；概率枚举说 p=0/1 的必须真的安全/是雷。
任何一条违反，实战里就会踩雷或漏标。

做法：
  1. 随机布雷
  2. 从某个安全格 flood fill 出「已开区」（模拟真实开局）
  3. 按真实雷布局算出每个已开格的数字
  4. 跑 solver，拿推断结果和真值比对
"""
from __future__ import annotations

import random
import sys
from typing import Dict, List, Set, Tuple

from solver import (collect_constraints, certain_moves, probabilities,
                    neighbors)

Cell = Tuple[int, int]


def gen_board(rows: int, cols: int, n_mines: int, rng: random.Random):
    """随机布雷 + flood fill 出已开区，返回 (grid, mines)。"""
    all_cells = [(r, c) for r in range(rows) for c in range(cols)]
    mines: Set[Cell] = set(rng.sample(all_cells, n_mines))

    def count(r, c):
        return sum(1 for p in neighbors(r, c, rows, cols) if p in mines)

    # 找一个安全且数字为 0 的格子开局（0 会连片展开）
    zeros = [(r, c) for r in range(rows) for c in range(cols)
             if (r, c) not in mines and count(r, c) == 0]
    if not zeros:
        return None, None
    start = rng.choice(zeros)

    opened: Set[Cell] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        if cur in opened or cur in mines:
            continue
        opened.add(cur)
        if count(*cur) == 0:
            for p in neighbors(*cur, rows, cols):
                if p not in opened and p not in mines:
                    stack.append(p)

    grid: List[List[str]] = [['#'] * cols for _ in range(rows)]
    for (r, c) in opened:
        n = count(r, c)
        grid[r][c] = '.' if n == 0 else str(n)
    return [''.join(row) for row in grid], mines


def run(trials=800, rows=9, cols=9, n_mines=10, seed=1234):
    rng = random.Random(seed)
    bad_certain = 0
    bad_prob = 0
    checked = 0
    prob_checked = 0
    skipped = 0
    for t in range(trials):
        grid, mines = gen_board(rows, cols, n_mines, rng)
        if grid is None:
            skipped += 1
            continue
        try:
            cons = collect_constraints(grid, rows, cols)
            m, s = certain_moves(cons)
        except ValueError as e:
            print(f'[冲突] trial {t}: {e}')
            bad_certain += 1
            continue
        checked += 1
        # 1) 必雷必须真是雷；必安全必须真安全
        wrong_m = m - mines
        wrong_s = s & mines
        if wrong_m or wrong_s:
            bad_certain += 1
            if bad_certain <= 3:
                print(f'[错判] trial {t}: 错标雷 {sorted(wrong_m)[:5]} '
                      f'错标安全 {sorted(wrong_s)[:5]}')
        # 2) 概率枚举的 0/1 必须和真值一致
        probs, diag = probabilities(grid, rows, cols)
        prob_checked += 1
        for c, p in probs.items():
            truth = c in mines
            if p >= 0.999 and not truth:
                bad_prob += 1
                if bad_prob <= 3: print(f'[概率错] trial {t}: {c} 判 p=1 但实际安全')
                break
            if p <= 0.001 and truth:
                bad_prob += 1
                if bad_prob <= 3: print(f'[概率错] trial {t}: {c} 判 p=0 但实际是雷')
                break
    print(f'\n跑 {trials} 局（跳过开局无 0 的 {skipped} 局）')
    print(f'  确定性推断错判: {bad_certain} / {checked}')
    print(f'  概率枚举 0/1 错判: {bad_prob} / {prob_checked}')
    print('  ' + ('全部通过 ✓' if bad_certain == 0 and bad_prob == 0 else '存在错误 ✗'))
    return bad_certain, bad_prob


if __name__ == '__main__':
    run(trials=int(sys.argv[1]) if len(sys.argv) > 1 else 800)
