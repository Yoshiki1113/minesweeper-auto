"""扫雷自动化：启动 / 停止 / 看状态 的开关（带 PID 文件，不用再找进程）。

用法：
    python ms.py start             # 后台启动，日志写 run.log，PID 记到 run.pid
    python ms.py start --no-uia    # 后面的参数会原样透传给 agent.py
    python ms.py stop              # 停止（按 PID 杀，不会误伤别的 python）
    python ms.py status            # 看是否在跑 + 日志尾部
    python ms.py log               # 只看日志尾部
    python ms.py watch             # 持续跟踪日志（Ctrl+C 退出）

也可以直接双击同目录的「启动.bat」「停止.bat」。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(HERE, 'run.pid')
LOG_FILE = os.path.join(HERE, 'run.log')
PY = r'D:\software\Anaconda3\python.exe'


def _read_pid() -> int:
    try:
        return int(open(PID_FILE, encoding='utf-8').read().strip())
    except Exception:
        return 0


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    out = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                         capture_output=True, text=True).stdout
    return str(pid) in out


def cmd_start(extra: list[str]) -> None:
    pid = _read_pid()
    if _alive(pid):
        print(f'已经在跑了（PID={pid}）。要停就执行： python ms.py stop')
        return
    # 清掉遗留的模态框，避免一起步就卡在弹窗上
    try:
        import win32api, win32con, win32gui
        for title, key in (('游戏胜利', 'P'), ('游戏失败', 'P'), ('新游戏', 'K')):
            h = win32gui.FindWindow(None, title)
            if h and win32gui.IsWindowVisible(h):
                win32gui.SetForegroundWindow(h)
                time.sleep(0.25)
                win32api.keybd_event(ord(key), 0, 0, 0)
                win32api.keybd_event(ord(key), 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(1.0)
                print(f'  清理残留弹窗 {title!r} → 按 {key}')
    except Exception:
        pass

    log = open(LOG_FILE, 'w', encoding='utf-8')
    # 赌博现在是 agent.py 的默认行为，不用再显式传 --guess（想关是 --no-guess）
    args = [PY, '-u', os.path.join(HERE, 'agent.py'), '--loop', '10'] + extra
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, cwd=HERE)
    with open(PID_FILE, 'w', encoding='utf-8') as f:
        f.write(str(p.pid))
    print(f'已启动 PID={p.pid}')
    print(f'  日志: {LOG_FILE}')
    print('  停止: python ms.py stop    （或双击「停止.bat」）')
    print('  注意：鼠标让出来，急停 = 把鼠标甩到屏幕左上角')


def cmd_stop() -> None:
    pid = _read_pid()
    if not _alive(pid):
        # PID 文件可能过期，兜底按命令行特征找
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "(Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object { $_.CommandLine -like '*agent.py*' -or "
             "$_.CommandLine -like '*--run-agent*' }).ProcessId"],
            capture_output=True, text=True).stdout
        pids = [int(x) for x in out.split() if x.strip().isdigit()]
        if not pids:
            print('没有在跑')
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
            return
    else:
        pids = [pid]
    for p in pids:
        subprocess.run(['taskkill', '/F', '/PID', str(p)], capture_output=True)
        print(f'已停止 PID={p}')
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def cmd_clean() -> None:
    """清掉残留弹窗（游戏失败/胜利 → P 开新局；新游戏确认框 → K 保住进度）。"""
    try:
        sys.path.insert(0, HERE)
        import agent as A
        h, kind = A.find_modal()
        if not kind:
            print('没有残留弹窗')
            return
        print(f'发现「{kind}」弹窗 → {A.handle_modal()}')
    except Exception as exc:
        print(f'清理失败: {type(exc).__name__}: {exc}')


def cmd_status() -> None:
    pid = _read_pid()
    if _alive(pid):
        print(f'状态：运行中（PID={pid}）')
        # 顺手报一下当前盘面
        try:
            import win32gui
            sys.path.insert(0, HERE)
            from identify_uia import UiaBoard
            h = win32gui.FindWindow(None, '扫雷')
            if h:
                b = UiaBoard(h)
                g = b.grid()
                print(f'  盘面：未开 {sum(r.count("#") for r in g)}  旗 {sum(r.count("F") for r in g)}')
        except Exception as e:
            print(f'  （盘面读取失败: {e}）')
    else:
        print('状态：未运行')
    cmd_log()


def cmd_log():
    if not os.path.exists(LOG_FILE):
        print('（还没有 run.log）')
        return
    print('\n=== run.log 尾部 ===')
    with open(LOG_FILE, encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    for ln in lines[-30:]:
        print(ln.rstrip())


def cmd_watch():
    print(f'跟踪 {LOG_FILE}（Ctrl+C 退出）')
    pos = 0
    while True:
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, encoding='utf-8', errors='replace') as f:
                    f.seek(pos)
                    for ln in f:
                        print(ln.rstrip(), flush=True)
                    pos = f.tell()
            time.sleep(0.5)
        except KeyboardInterrupt:
            return


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    extra = sys.argv[2:]
    {'start': lambda: cmd_start(extra),
     'stop': cmd_stop,
     'clean': cmd_clean,
     'status': cmd_status,
     'log': cmd_log,
     'watch': cmd_watch}.get(action, cmd_status)()
