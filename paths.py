#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路径解析 —— 让同一份代码在「开发目录」和「PyInstaller 打包的单 exe」里都能跑。

开发时：脚本所在目录（如 D:\\code\\minesweeper\\），日志与 PID 写在同目录。
打包后：只读资源（Minesweeper.exe、.py）在 sys._MEIPASS 临时解包目录；
        可写数据（run.log、run.pid）写在 exe 旁边，方便用户看到。
"""
import os
import sys


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的 exe 里。"""
    return bool(getattr(sys, 'frozen', False))


def resource_dir() -> str:
    """只读资源目录（打包后 = _MEIPASS）。"""
    if is_frozen():
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """可写数据目录（日志 / PID 放这里）。"""
    if is_frozen():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource(*parts: str) -> str:
    return os.path.join(resource_dir(), *parts)


def data(*parts: str) -> str:
    return os.path.join(data_dir(), *parts)


def minesweeper_exe() -> str:
    """扫雷程序位置：优先用随包携带的那份（迁移到别的电脑也能跑），
    找不到才回退到当前用户的「文档\\扫雷\\」。"""
    bundled = resource('Minesweeper.exe')
    if os.path.exists(bundled):
        return bundled
    # 回退路径按**当前用户**推导。原来这里写死 C:\Users\spp\...，
    # 那是别的机器上留下的用户名，本机（C:\Users\sp）根本不存在 → 永远找不到。
    fallback = os.path.join(os.path.expanduser('~'), 'Documents', '扫雷', 'Minesweeper.exe')
    return fallback if os.path.exists(fallback) else bundled


def self_exe() -> str:
    """启动子进程时用的可执行文件。

    打包后就是本 exe（子进程带 --run-agent 参数即跑 agent）；
    开发时是当前 Python 解释器。
    """
    if is_frozen():
        return sys.executable
    return sys.executable
