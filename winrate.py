# -*- coding: utf-8 -*-
"""连打 N 局统计胜率。

用法:
    python winrate.py                # 默认 100 局
    python winrate.py -n 20
    python winrate.py -n 100 --csv log/winrate.csv
    python winrate.py -n 100 --no-guess    # 禁止赌博（无确定步就停）

注意：这里**不能**用 agent.run(loop=N) —— 它赢了就 break，统计不了胜率。
所以自己写循环，逐局调用 play_game，并在两局之间做「确认重置 + 重新枚举」。

默认开赌博（allow_guess=True），和 agent.py 的 --guess 一致 —— 不开的话无确定步就停手，
那 5 次「不得不猜」会变成停在这里，统计出来的不是胜率。
"""
import argparse
import csv
import sys
import time
from collections import Counter

import agent
from identify import COLS, ROWS


def between_games(hwnd, total_secs):
    """两局之间：确认棋盘已重置，并重新枚举 UIA。

    ⚠️ 必须重新枚举：按 P 开新局后 UIA 元素引用会失效，CurrentName 一直返回
    上一局的旧数据。不重新枚举的话插旗校验会误判「没插上」，补标反而把旗取消，
    最后触发熔断 —— 表现为「连打第二局必挂」。
    另外这里只能用像素读判断是否重置，因为此刻 UIA 不可信。
    """
    time.sleep(1.0)
    agent._TODO['cells'] = None
    pg = agent._pixel_board()
    n_now = sum(r.count('#') for r in pg) if pg is not None else None
    if n_now == ROWS * COLS:
        u = agent._READER['uia']
        if u is not None:
            t0 = time.perf_counter()
            u.enumerate_all()
            total_secs['enum'] += time.perf_counter() - t0
            agent._TODO['cells'] = None
        return hwnd, True
    print(f'      棋盘没重置（未开 {n_now}）→ 走 restart_game', flush=True)
    new = agent.restart_game(hwnd)
    return (new or hwnd), False


def main():
    ap = argparse.ArgumentParser(description='连打 N 局统计胜率')
    ap.add_argument('-n', '--games', type=int, default=100, help='打几局（默认 100）')
    ap.add_argument('--csv', default=None, help='把每局明细写成 CSV')
    ap.add_argument('--max-steps', type=int, default=600)
    ap.add_argument('--no-guess', action='store_true', help='禁止赌博')
    args = ap.parse_args()

    hwnd = agent.win32gui.FindWindow(None, agent.WINDOW_TITLE)
    if not hwnd:
        print('[x] 扫雷没在跑')
        return 1

    agent.init_reader(hwnd, use_uia=True)
    h2 = agent.restart_game(hwnd)
    if h2:
        hwnd = h2
    if agent._READER['uia'] is None:
        agent.init_reader(hwnd, use_uia=True)
    agent._TODO['cells'] = None
    agent.read_grid(force_full=True)

    rows = []
    tally = Counter()
    total_secs = {'enum': 0.0}
    t_start = time.time()

    print(f'\n{"="*60}\n连打 {args.games} 局   赌博 {"禁止" if args.no_guess else "允许"}'
          f'\n{"="*60}', flush=True)

    try:
        for i in range(1, args.games + 1):
            agent._TIMING.clear()
            res, steps = agent.play_game(hwnd, False, not args.no_guess,
                                         args.max_steps, None)
            tot = sum(agent._TIMING.values())
            tally[res] += 1
            n = sum(tally.values())
            rows.append({
                '局': i, '结果': res, '步数': steps,
                '耗时s': round(tot, 2),
                'ms每步': round(tot / max(1, steps) * 1000),
            })
            el = time.time() - t_start
            print(f'>>> 第 {i}/{args.games}  {res:<6} {steps:4d} 步  '
                  f'{tot:5.1f}s  |  累计 胜 {tally["win"]}/{n} = '
                  f'{tally["win"]/n:5.1%}  |  已用 {el/60:.1f} 分  '
                  f'预计还需 {(el/n)*(args.games-n)/60:.0f} 分', flush=True)

            if i < args.games:
                hwnd, _ = between_games(hwnd, total_secs)
    except KeyboardInterrupt:
        print('\n[!] 被中断，下面是已打部分的结果', flush=True)

    n = sum(tally.values())
    el = time.time() - t_start
    print(f'\n{"="*60}\n结果（{n} 局）\n{"="*60}')
    for k in ('win', 'lost', 'stuck', 'draw', 'stopped'):
        if tally.get(k):
            print(f'  {k:<8} {tally[k]:4d}  {tally[k]/n:6.1%}')
    print(f'\n  胜率 {tally["win"]}/{n} = {tally["win"]/max(1,n):.2%}')
    print(f'  总耗时 {el/60:.1f} 分（其中 UIA 重新枚举 {total_secs["enum"]:.0f}s）')
    print(f'  平均 {el/max(1,n):.1f} s/局')
    if rows:
        ms = [r['ms每步'] for r in rows if r['步数']]
        st = [r['步数'] for r in rows]
        if ms:
            print(f'  每步中位 {sorted(ms)[len(ms)//2]} ms    '
                  f'步数中位 {sorted(st)[len(st)//2]}')
    print('\n  参考：同一个 solver 的离线模拟（1000 局，按真实首点规则）是 42.1%；'
          '实机低的那几个点是自动化开销（丢焦点、偶发 chord 失效）')

    if args.csv and rows:
        with open(args.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f'  明细已写 {args.csv}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
