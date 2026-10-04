"""用 solver 离线模拟整局扫雷，统计胜率。

不碰真实窗口、不点鼠标，纯逻辑模拟，可以跑几千局。
是速度优化前的基线对照（旧：12.7 s/步，新：271 ms/步）和胜率上限实验台。
统计口径：
  胜率      赢了/总局数
  猜局率    含至少一次「无确定步」的局占比
  猜测步数  平均每局被迫猜几次
"""
from __future__ import annotations

import random
import sys
import time
from typing import List, Set, Tuple

from solver import collect_constraints, certain_moves, probabilities, neighbors

Cell = Tuple[int, int]


class Board:
    def __init__(self, rows: int, cols: int, n_mines: int, rng: random.Random):
        self.rows, self.cols, self.n_mines = rows, cols, n_mines
        allc = [(r, c) for r in range(rows) for c in range(cols)]
        self.mines: Set[Cell] = set(rng.sample(allc, n_mines))
        self.opened: Set[Cell] = set()
        self.flagged: Set[Cell] = set()
        self.dead = False

    def count(self, r, c) -> int:
        return sum(1 for p in neighbors(r, c, self.rows, self.cols) if p in self.mines)

    def open(self, r, c) -> bool:
        """点开一格。返回 False 表示踩雷。含 0 连片展开。"""
        if (r, c) in self.mines:
            self.dead = True
            return False
        stack = [(r, c)]
        while stack:
            cur = stack.pop()
            if cur in self.opened:
                continue
            self.opened.add(cur)
            if self.count(*cur) == 0:
                for p in neighbors(*cur, self.rows, self.cols):
                    if p not in self.opened and p not in self.mines:
                        stack.append(p)
        return True

    def open_random_safe(self, rng) -> None:
        """开局：随便点一个安全格（真实游戏里首点必不踩雷）。"""
        safe = [(r, c) for r in range(self.rows) for c in range(self.cols)
                if (r, c) not in self.mines]
        self.open(*rng.choice(safe))

    def grid(self) -> List[str]:
        out = []
        for r in range(self.rows):
            row = []
            for c in range(self.cols):
                if (r, c) in self.flagged:
                    row.append('F')
                elif (r, c) in self.opened:
                    n = self.count(r, c)
                    row.append('.' if n == 0 else str(n))
                else:
                    row.append('#')
            out.append(''.join(row))
        return out

    def solved(self) -> bool:
        return len(self.opened) == self.rows * self.cols - self.n_mines


def play(rows, cols, n_mines, rng, max_steps=4000, mode='solver'):
    b = Board(rows, cols, n_mines, rng)
    b.open_random_safe(rng)
    steps = guesses = 0
    while not b.dead and not b.solved() and steps < max_steps:
        if mode == 'random':
            # 真正的纯随机基线：完全不用 solver，每步随便点一个未开格
            cands = [(r, c) for r in range(rows) for c in range(cols)
                     if (r, c) not in b.opened and (r, c) not in b.flagged]
            if not cands:
                break
            guesses += 1
            b.open(*rng.choice(cands))
            steps += 1
            continue
        grid = b.grid()
        cons = collect_constraints(grid, rows, cols)
        try:
            mines, safes = certain_moves(cons)
        except ValueError:
            return {'result': 'conflict', 'steps': steps, 'guesses': guesses}
        if safes:
            for c in sorted(safes):
                if c not in b.opened:
                    b.open(*c)
                    break
        elif mines:
            for c in sorted(mines):
                b.flagged.add(c)
                break
        else:
            # 无确定步 → 猜
            guesses += 1
            probs, _ = probabilities(grid, rows, cols,
                                     remaining_mines=n_mines - len(b.flagged))
            cands = [c for c in probs if c not in b.opened and c not in b.flagged]
            if not cands:
                break
            cell = min(cands, key=lambda c: probs[c])
            b.open(*cell)
        steps += 1
    if b.solved():
        res = 'win'
    elif b.dead:
        res = 'boom'
    else:
        res = 'stuck'
    return {'result': res, 'steps': steps, 'guesses': guesses}


def run(rows, cols, n_mines, trials, seed=7, mode='solver', label=''):
    rng = random.Random(seed)
    stats = {}
    gsum = 0
    t0 = time.time()
    for i in range(trials):
        r = play(rows, cols, n_mines, rng, mode=mode)
        stats[r['result']] = stats.get(r['result'], 0) + 1
        gsum += r['guesses']
    dt = time.time() - t0
    win = stats.get('win', 0)
    print(f"\n=== {label or mode}  {rows}x{cols} / {n_mines} 雷 / {trials} 局 ({(time.time()-t0):.1f}s) ===")
    print(f"  胜率      {win/trials:6.2%}   ({win}/{trials})")
    print(f"  踩雷      {stats.get('boom',0)/trials:6.2%}")
    print(f"  卡住/冲突 {stats.get('stuck',0)/trials:6.2%}  {stats.get('conflict',0)/trials:6.2%}")
    print(f"  平均猜测  {gsum/trials:.2f} 次/局")
    return win / trials


if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    run(9, 9, 10, n, label='solver')
    run(9, 9, 10, n, mode='random', label='random(基线)')
