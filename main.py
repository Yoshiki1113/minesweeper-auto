#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统一入口 —— 一个 exe 承担两种角色。

    扫雷AI.exe                       → 打开控制台窗口
    扫雷AI.exe --run-agent --loop 5  → 后台跑 agent（由控制台自己拉起自己）

打包成单一 exe 后：不需要目标机器装 Python，也不依赖任何绝对路径，
扫雷程序（Minesweeper.exe）随包携带，迁移到别的电脑直接能用。
"""
import os
import sys

# 控制台/管道可能是 GBK，先统一成 UTF-8，避免 print 到 ⏱ 🎉 这类字符时直接崩。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 打包后模块解在 _MEIPASS；开发时在脚本目录。两者都放进 sys.path。
_BASE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)


def run_agent() -> None:
    """后台 agent 角色。"""
    sys.argv = [a for a in sys.argv if a != '--run-agent']
    import agent
    agent.main()


def run_gui() -> None:
    """控制台角色。"""
    import ms_gui
    ms_gui.main()


if __name__ == '__main__':
    if '--run-agent' in sys.argv:
        run_agent()
    else:
        run_gui()
