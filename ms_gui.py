#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫雷 AI 控制台 —— 把「启动 / 停止 / 状态 / 日志 / 指标」整合到一个窗口里。

设计令牌取自 ui-ux-pro-max skill（产品类型：Developer Tool / IDE，深色）：
    背景 #0F172A   卡片 #1B2336   正文 #F8FAFC   次级文字 #94A3B8
    主色 #1E293B   边框 #475569   运行绿 #22C55E   停止红 #EF4444
字体配对参考该 skill 的 "Dashboard Data"（Mono + Sans）：
    中文 Microsoft YaHei UI，数字/日志 Consolas（等宽对齐）。

用法：双击「扫雷控制台.bat」，或 python ms_gui.py
"""
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk

import paths

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = paths.resource_dir()      # 只读资源（agent.py / ms.py）
DATA = paths.data_dir()         # 可写数据（run.log / run.pid）
LOG = paths.data('run.log')
PIDF = paths.data('run.pid')
PY = paths.self_exe()

# ---- 设计令牌 ----
C = {
    'bg':       '#0F172A',
    'card':     '#1B2336',
    'fg':       '#F8FAFC',
    'muted':    '#272F42',
    'muted_fg': '#94A3B8',
    'border':   '#475569',
    'primary':  '#1E293B',
    'accent':   '#22C55E',
    'danger':   '#EF4444',
    'warn':     '#F59E0B',
}
FONT_UI = ('Microsoft YaHei UI', 10)
FONT_UI_B = ('Microsoft YaHei UI', 10, 'bold')
FONT_TITLE = ('Microsoft YaHei UI', 15, 'bold')
FONT_MONO = ('Consolas', 9)
FONT_BIG = ('Consolas', 20, 'bold')
FONT_NUM = ('Consolas', 15, 'bold')


def _no_window() -> dict:
    """Windows 下别弹出黑框。"""
    if os.name != 'nt':
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {'startupinfo': si, 'creationflags': 0x08000000}


def read_pid():
    try:
        with open(PIDF) as f:
            p = int(f.read().strip())
        out = subprocess.run(['tasklist', '/FI', f'PID eq {p}'],
                             capture_output=True, text=True, **_no_window())
        if str(p) in out.stdout:
            return p
    except Exception:
        pass
    return 0


def parse_log(text: str) -> dict:
    """从日志里抠出指标（给面板用）。"""
    d = {'game': 0, 'step': 0, 'unopened': 0, 'ms': 0, 'win': 0, 'lost': 0,
         'breakdown': {}, 'result': '', 'final': False}
    for m in re.finditer(r'===== 第 (\d+) 局 =====', text):
        d['game'] = int(m.group(1))
    for m in re.finditer(r'^\[(\d+)\]', text, re.M):
        d['step'] = int(m.group(1))
    for m in re.finditer(r'未开 (\d+)', text):
        d['unopened'] = int(m.group(1))
    for m in re.finditer(r'第 \d+ 局结束: (\w+)\s+步数 (\d+)', text):
        d['result'] = m.group(1)
        d['step'] = int(m.group(2))
        if m.group(1) == 'win':
            d['win'] += 1
        elif m.group(1) == 'lost':
            d['lost'] += 1
    m = re.search(r'平均 (\d+) ms/步', text)
    if m:
        d['ms'] = int(m.group(1))
        d['final'] = True
    for m in re.finditer(r'^\s{6}(\S+)\s+[\d.]+s\s+[\d.]+%\s+\(\s*(\d+) ms/步\)',
                         text, re.M):
        d['breakdown'][m.group(1)] = int(m.group(2))
    return d


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title('扫雷 AI 控制台')
        root.configure(bg=C['bg'])
        # 按屏幕 DPI 缩放窗口：高分屏上系统会把 940x660 再缩一道，
        # 结果内容溢出、右边和底部被裁掉（实测 940x660 只剩 766x566）。
        try:
            scale = max(1.0, root.winfo_fpixels('1i') / 96.0)
        except Exception:
            scale = 1.0
        root.geometry(f'{int(1060 * scale)}x{int(690 * scale)}+60+60')
        root.minsize(int(940 * scale), int(600 * scale))
        # 打包成 exe 后 tk 有时把窗口建到屏幕外（实测 -25600,-25600）——
        # 显式摆正并置前，否则用户根本看不到窗口。
        root.update_idletasks()
        try:
            root.deiconify()
            root.lift()
            root.attributes('-topmost', True)
            root.after(600, lambda: root.attributes('-topmost', False))
        except Exception:
            pass
        self._tail = 0
        self._lines = 0
        self._build()
        self._tick()

    # ---------- 界面 ----------
    def _card(self, parent, **kw):
        return tk.Frame(parent, bg=C['card'], highlightthickness=1,
                        highlightbackground=C['border'], **kw)

    def _btn(self, parent, text, cmd, color, width=14):
        """按钮：最小 44px 高（可点区域），带 hover 反馈。"""
        b = tk.Button(parent, text=text, command=cmd, font=FONT_UI_B,
                      bg=color, fg=C['fg'], activebackground=C['border'],
                      activeforeground=C['fg'], relief='flat', bd=0,
                      width=width, height=2, cursor='hand2')
        b.bind('<Enter>', lambda e: b.config(bg=C['muted'] if color == C['muted']
                                             else self._light(color)))
        b.bind('<Leave>', lambda e: b.config(bg=color))
        return b

    @staticmethod
    def _light(hexcolor):
        r, g, b = (int(hexcolor[i:i + 2], 16) for i in (1, 3, 5))
        return '#%02X%02X%02X' % (min(255, r + 28), min(255, g + 28), min(255, b + 28))

    def _build(self):
        # ── 顶栏（用 grid：pack(side='right') 在内容偏宽时会被挤出可视区）──
        top = tk.Frame(self.root, bg=C['bg'])
        top.pack(fill='x', padx=18, pady=(16, 8))
        top.grid_columnconfigure(0, weight=1)
        tk.Label(top, text='扫雷 AI 控制台', font=FONT_TITLE,
                 bg=C['bg'], fg=C['fg']).grid(row=0, column=0, sticky='w')
        self.lamp = tk.Label(top, text='  ●  已停止  ', font=FONT_UI_B,
                             bg=C['muted'], fg=C['muted_fg'], padx=10, pady=5)
        self.lamp.grid(row=0, column=1, sticky='e')

        # ── 按钮行 ──
        bar = tk.Frame(self.root, bg=C['bg'])
        bar.pack(fill='x', padx=18, pady=(4, 12))
        bar.grid_columnconfigure(0, weight=1)
        btns = tk.Frame(bar, bg=C['bg'])
        btns.grid(row=0, column=0, sticky='w')
        self.b_start = self._btn(btns, '▶  启 动', self.start, C['accent'])
        self.b_start.pack(side='left')
        self.b_stop = self._btn(btns, '■  停 止', self.stop, C['danger'])
        self.b_stop.pack(side='left', padx=10)
        self.b_clean = self._btn(btns, '清理盘面', self.clean, C['muted'], 10)
        self.b_clean.pack(side='left')
        self.b_log = self._btn(btns, '清空日志', self.clearlog, C['muted'], 10)
        self.b_log.pack(side='left', padx=10)

        # 局数选择
        gb = tk.Frame(bar, bg=C['bg'])
        gb.grid(row=0, column=1, sticky='e')
        tk.Label(gb, text='局数', font=FONT_UI, bg=C['bg'],
                 fg=C['muted_fg']).pack(side='left', padx=(0, 6))
        self.vars_loop = tk.StringVar(value='5')
        for n in ('1', '5', '10', '99'):
            tk.Radiobutton(gb, text=n, value=n, variable=self.vars_loop,
                           font=FONT_MONO, bg=C['bg'], fg=C['muted_fg'],
                           selectcolor=C['card'], activebackground=C['bg'],
                           activeforeground=C['fg'], bd=0,
                           highlightthickness=0).pack(side='left')

        # ── 指标卡 ──
        row = tk.Frame(self.root, bg=C['bg'])
        row.pack(fill='x', padx=18)
        # Tk 默认让 Frame 跟着子控件“长大”：grid 列的请求宽度会把卡片撑到 340px、
        # 4 张就超窗口，最后一列被顶出去。锁死 propagate 与高度就老实了。
        row.grid_propagate(False)
        row.config(height=74)
        self.metrics = {}
        # 只放 4 张：Tk 的 grid 列宽由内容撑开，5 列会超出窗口把最后一列顶出去。
        # 「未开格」放到下面那行跟耗时并列。
        specs = [('game', '当前局'), ('step', '本局步数'),
                 ('ms', 'ms / 步'), ('score', '胜 / 负')]
        for i, (key, label) in enumerate(specs):
            row.grid_columnconfigure(i, weight=0)      # 不平分：平分时列请求宽度会互相撑开
            card = self._card(row)
            card.grid(row=0, column=i, sticky='w', padx=(0 if i == 0 else 6, 0))
            card.grid_propagate(False)
            card.config(width=248, height=72)          # 固定尺寸，不让内容说了算
            tk.Label(card, text=label, font=('Microsoft YaHei UI', 9), bg=C['card'],
                     fg=C['muted_fg']).pack(anchor='w', padx=10, pady=(8, 0))
            v = tk.Label(card, text='—', font=('Consolas', 14, 'bold'),
                         bg=C['card'], fg=C['fg'])
            v.pack(anchor='w', padx=10, pady=(0, 8))
            self.metrics[key] = v
        self.metrics['game'].config(text='0')
        self.metrics['step'].config(text='0')
        self.metrics['score'].config(text='0 / 0')

        # ── 各环节耗时 ──
        self.brk = tk.Label(self.root, text='各环节耗时：等待数据…', font=FONT_MONO,
                            bg=C['bg'], fg=C['muted_fg'], justify='left', anchor='w')
        self.brk.pack(fill='x', padx=20, pady=(10, 6))

        # ── 日志 ──
        box = self._card(self.root)
        box.pack(fill='both', expand=True, padx=18, pady=(4, 6))
        head = tk.Frame(box, bg=C['card'])
        head.pack(fill='x', padx=12, pady=(8, 0))
        tk.Label(head, text='运行日志', font=FONT_UI_B, bg=C['card'],
                 fg=C['muted_fg']).pack(side='left')
        self.lbl_log = tk.Label(head, text='run.log', font=FONT_MONO,
                                bg=C['card'], fg=C['border'])
        self.lbl_log.pack(side='right')
        self.txt = tk.Text(box, bg=C['card'], fg=C['fg'], font=FONT_MONO,
                           relief='flat', bd=0, wrap='none', insertbackground=C['fg'],
                           padx=12, pady=6, height=14)
        sb = tk.Scrollbar(box, command=self.txt.yview, bg=C['muted'],
                          troughcolor=C['card'], bd=0, relief='flat')
        self.txt.config(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.txt.pack(fill='both', expand=True)
        for tag, col in (('win', C['accent']), ('lost', C['danger']),
                         ('warn', C['warn']), ('dim', C['muted_fg'])):
            self.txt.tag_config(tag, foreground=col)

        # ── 底栏 ──
        tk.Label(self.root,
                 text='急停：把鼠标甩到屏幕左上角 6×6 区域   ｜   '
                      '停止会立刻杀掉 agent 进程，不影响扫雷本身',
                 font=FONT_UI, bg=C['bg'], fg=C['border']).pack(pady=(0, 10))
        self.root.after(1500, self._dbg)

    def _dbg(self):
        """调试：把各控件的实际几何写出来（找不到控件时看这个）。"""
        try:
            with open(os.path.join(HERE, 'gui_debug.txt'), 'w',
                      encoding='utf-8') as f:
                f.write(f'root {self.root.winfo_width()}x{self.root.winfo_height()}\n')
                f.write(f'lamp mapped={self.lamp.winfo_ismapped()} '
                        f'xy=({self.lamp.winfo_x()},{self.lamp.winfo_y()}) '
                        f'{self.lamp.winfo_width()}x{self.lamp.winfo_height()}\n')
                f.write(f'root reqwidth={self.root.winfo_reqwidth()}\n')
        except Exception:
            pass

    # ---------- 动作 ----------
    def _spawn(self, args):
        """起一个后台任务。

        打包后没有单独的 python + ms.py，所以让 exe 自己再跑一遍（--run-agent）；
        开发时就用当前解释器跑 ms.py。
        """
        if paths.is_frozen():
            cmd = [paths.self_exe(), '--run-agent'] + list(args)
            cwd = paths.data_dir()
        else:
            cmd = [paths.self_exe(),
                   os.path.join(paths.resource_dir(), 'ms.py')] + list(args)
            cwd = paths.resource_dir()
        # --windowed 打包后子进程没有控制台，agent 的 print 必须落到文件里，
        # 否则界面上的“运行日志”一片空白。
        logf = open(LOG, 'a', encoding='utf-8', errors='replace')
        return subprocess.Popen(cmd, cwd=cwd, stdout=logf,
                                stderr=subprocess.STDOUT, **_no_window())

    def start(self):
        if read_pid():
            self._say('[控制台] 已经在运行了', 'warn')
            return
        n = self.vars_loop.get()
        try:
            self._spawn(['--loop', n])
            self._say(f'[控制台] 启动 agent（最多 {n} 局）…', 'dim')
        except Exception as exc:
            self._say(f'[控制台] 启动失败：{exc}', 'lost')

    def stop(self):
        pid = read_pid()
        if not pid:
            self._say('[控制台] 没有在运行', 'warn')
            return
        try:
            subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                           capture_output=True, **_no_window())
            try:
                os.remove(PIDF)
            except Exception:
                pass
            self._say(f'[控制台] 已停止 PID {pid}', 'warn')
        except Exception as exc:
            self._say(f'[控制台] 停止失败：{exc}', 'lost')

    def clean(self):
        """清掉残留弹窗（游戏失败/胜利 → P，新游戏确认框 → K）。"""
        try:
            if paths.resource_dir() not in sys.path:
                sys.path.insert(0, paths.resource_dir())
            import agent as A
            h, kind = A.find_modal()
            if not kind:
                self._say('[控制台] 没有残留弹窗', 'dim')
                return
            self._say(f'[控制台] 发现「{kind}」弹窗 → {A.handle_modal()}', 'warn')
        except Exception as exc:
            self._say(f'[控制台] 清理失败：{exc}', 'lost')

    def clearlog(self):
        try:
            open(LOG, 'w').close()
        except Exception:
            pass
        self.txt.delete('1.0', 'end')
        self._tail = 0
        self._say('[控制台] 日志已清空', 'dim')

    def _say(self, s: str, tag='dim'):
        self.txt.insert('end', s + '\n', tag)
        self.txt.see('end')

    # ---------- 轮询 ----------
    def _tick(self):
        pid = read_pid()
        running = pid > 0
        # 状态灯
        self.lamp.config(text=f'  ●  运行中 PID {pid}  ' if running else '  ●  已停止  ',
                         bg='#14532D' if running else C['muted'],
                         fg=C['accent'] if running else C['muted_fg'])
        # 日志增量
        try:
            size = os.path.getsize(LOG)
            if size < self._tail:
                self._tail = 0          # 被清空
            if size > self._tail:
                with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(self._tail)
                    new = f.read()
                    self._tail = f.tell()
                for ln in new.splitlines():
                    tag = 'dim'
                    if '通关' in ln or 'win' in ln.lower():
                        tag = 'win'
                    elif '失败' in ln or 'lost' in ln.lower() or '★' in ln:
                        tag = 'lost'
                    elif '⚠' in ln or '重试' in ln:
                        tag = 'warn'
                    self.txt.insert('end', ln + '\n', tag)
                    self._lines += 1
                if self._lines > 4000:      # 别让 Text 无限长
                    self.txt.delete('1.0', '500.0')
                    self._lines -= 500
                self.txt.see('end')
                with open(LOG, 'r', encoding='utf-8', errors='replace') as f:
                    d = parse_log(f.read())
                self._apply(d)
        except FileNotFoundError:
            pass
        except Exception:
            pass
        self.root.after(700, self._tick)

    def _apply(self, d: dict):
        self.metrics['game'].config(text=str(d['game']))
        self.metrics['step'].config(text=str(d['step']))
        self.metrics['ms'].config(
            text=str(d['ms']) if d['ms'] else '—',
            fg=C['accent'] if d['ms'] and d['ms'] <= 400 else
               (C['warn'] if d['ms'] else C['fg']))
        self.metrics['score'].config(text=f"{d['win']} / {d['lost']}")
        left = f'未开格 {d["unopened"]}'
        if d['breakdown']:
            parts = '   '.join(f'{k} {v}ms' for k, v in
                               sorted(d['breakdown'].items(), key=lambda kv: -kv[1]))
            self.brk.config(text=f'{left}   ｜   各环节耗时（每步）：' + parts,
                            fg=C['muted_fg'])
        elif d['step']:
            self.brk.config(text=f'{left}   ｜   第 {d["step"]} 步进行中…',
                            fg=C['muted_fg'])


def main():
    # DPI 感知必须在**创建任何窗口之前**设置，否则不生效（窗口会被系统再缩一道，
    # geometry 写 940x660 实际只得到 766x566，内容溢出被裁）。
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
