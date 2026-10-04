# -*- coding: utf-8 -*-
"""用 ffmpeg 录「扫雷」窗口，60fps。

**停止方式决定文件能不能用**，这是这个脚本存在的主要原因：
  * 裸 ffmpeg + taskkill 强杀：分片 MP4 虽然能打开，但最后一个分片是截断的
    —— 实测 ffmpeg 解码报 `Invalid NAL unit size`，严格些的播放器会直接拒开。
  * 这里走管道给 ffmpeg 送 'q'，让它自己收尾 → 产出完整、带 moov 的 MP4，
    再加 `-movflags +faststart` 把索引挪到文件头，随便拖进度条。

停止方式（三选一）：
    1) 运行 停录.bat        —— 它只是建一个 record\\STOP 标志文件
    2) 在窗口里按 Ctrl+C
    3) python record.py --seconds 30   —— 只录 30 秒

为什么窗口标题放在 Python 里而不是 bat 里：cmd 的代码页很容易把「扫雷」搞乱，
Python 传 Unicode 参数给 ffmpeg 是可靠的。
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

TITLE = '扫雷'                     # 窗口标题（gdigrab 按标题抓，跟着窗口走）
FPS = 60
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, 'record')
STOP_FILE = os.path.join(OUTDIR, 'STOP')

FF_CANDIDATES = [
    os.path.join(os.path.expanduser('~'), 'anaconda3', 'Lib', 'site-packages',
                 'ffmpeg-6.0', 'bin', 'ffmpeg.exe'),
    os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'WinGet',
                 'Links', 'ffmpeg.exe'),
    r'C:\ffmpeg\bin\ffmpeg.exe',
    os.path.join(os.path.expanduser('~'), 'scoop', 'shims', 'ffmpeg.exe'),
]


def find_ffmpeg():
    p = shutil.which('ffmpeg')
    if p:
        return p
    for c in FF_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return None


# 硬件编码器：**编译进去不等于能用**。实测本机 nvenc 缺 nvcuda.dll、
# amf 缺 amfrt64.dll，只有 qsv 能跑（Intel Arc）。所以必须真跑一次才算数。
HW_ENCODERS = [
    ('h264_qsv', ['-c:v', 'h264_qsv', '-global_quality', '22']),
    ('h264_nvenc', ['-c:v', 'h264_nvenc', '-cq', '23']),
    ('h264_amf', ['-c:v', 'h264_amf', '-quality', 'speed']),
]
SW_ENCODER = ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18']


def detect_hw(ff):
    """试出一个真能用的硬件编码器，返回 (名字, 参数)；都不行就返回 (None, 软编参数)。

    省得不多（实测 libx264 ultrafast 0.87 核 vs qsv 0.64 核）—— 因为瓶颈在
    gdigrab 抓取本身（每帧一次全窗口 BitBlt + RGB→YUV），不在编码器。
    """
    for name, args in HW_ENCODERS:
        try:
            r = subprocess.run(
                [ff, '-hide_banner', '-loglevel', 'error',
                 '-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.2', '-r', '30']
                + args + ['-f', 'null', '-'],
                capture_output=True, timeout=30)
            if r.returncode == 0:
                return name, args
        except Exception:
            continue
    return None, SW_ENCODER


def window_exists(title=TITLE):
    """扫雷在不在跑。用 win32gui（找不到就退化成「假定在」，让 ffmpeg 自己报错）。"""
    try:
        import win32gui
        return bool(win32gui.FindWindow(None, title))
    except Exception:
        return True


def build_cmd(ff, out, seconds=None, encoder=None):
    # -framerate 60     目标 60 帧
    # title=扫雷         按窗口标题抓：跟着窗口走，且不受 DPI 缩放影响
    # scale=trunc(...)   宽高取偶数，否则 yuv420p 编不了（实测抓到 1307，是奇数）
    # -g 120             每 2 秒一个关键帧，方便拖进度
    # +faststart         把 moov 挪到文件头（收尾时做，所以要能优雅停止）
    cmd = [ff, '-hide_banner', '-loglevel', 'warning', '-stats',
           '-f', 'gdigrab', '-framerate', str(FPS), '-i', f'title={TITLE}',
           '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', '-pix_fmt', 'yuv420p']
    cmd += encoder or SW_ENCODER
    cmd += ['-g', '120', '-movflags', '+faststart']
    if seconds:
        cmd += ['-t', str(seconds)]
    cmd += ['-y', out]
    return cmd


def main():
    ap = argparse.ArgumentParser(description='录「扫雷」窗口（60fps）')
    ap.add_argument('--stop', action='store_true',
                    help='给正在录的那个进程发停止信号，然后退出')
    ap.add_argument('--seconds', type=float, default=None,
                    help='只录这么多秒（默认一直录到停止信号）')
    ap.add_argument('--title', default=TITLE, help=f'窗口标题（默认 {TITLE}）')
    ap.add_argument('--fps', type=int, default=FPS, help=f'帧率（默认 {FPS}）')
    ap.add_argument('--crf', type=int, default=18, help='质量，越小越好（默认 18）')
    ap.add_argument('--hw', action='store_true',
                    help='用硬件编码（自动试 qsv/nvenc/amf，省约 0.2 个核；'
                         '默认用 libx264，体积和质量更好）')
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)

    if args.stop:
        # 停止逻辑放在 Python 里，bat 就能保持纯 ASCII
        # （cmd 即使 chcp 65001 也不能可靠处理 bat 里的中文 —— 实测 rem/echo
        #   里的中文会被拆成命令去执行）
        open(STOP_FILE, 'w').close()
        print('  已发出停止信号，等 ffmpeg 收尾...')
        for _ in range(200):                 # 最多等 20 秒
            if not os.path.exists(STOP_FILE):
                print('  已停止。文件在 record\\ 目录。')
                return 0
            time.sleep(0.1)
        print('  [!] 等了 20 秒没停 —— 可能本来就没在录。')
        return 1

    ff = find_ffmpeg()
    if not ff:
        print('[x] 找不到 ffmpeg.exe')
        print('    装一个：winget install Gyan.FFmpeg')
        print('    或把它所在目录加到 PATH')
        return 1

    if not window_exists(args.title):
        print(f'[x] 找不到窗口「{args.title}」，扫雷没在跑吧？')
        return 1

    os.makedirs(OUTDIR, exist_ok=True)
    # 上一次没收干净的停止标志会让我们刚开录就停 —— 先清掉
    if os.path.exists(STOP_FILE):
        try:
            os.remove(STOP_FILE)
        except OSError:
            pass

    out = os.path.join(OUTDIR, f'{args.title}-{datetime.now():%Y%m%d-%H%M%S}.mp4')
    encoder, enc_name = SW_ENCODER, 'libx264'
    if args.hw:
        enc_name, encoder = detect_hw(ff)
        if enc_name is None:
            print('  [!] 没找到能用的硬件编码器，退回 libx264')
            enc_name = 'libx264'
    cmd = build_cmd(ff, out, args.seconds, encoder)
    if args.fps != FPS:
        cmd[cmd.index(str(FPS))] = str(args.fps)
    if not args.hw and args.crf != 18:
        cmd[cmd.index('-crf') + 1] = str(args.crf)

    print(f'  ffmpeg : {ff}')
    print(f'  窗口   : {args.title}   {args.fps} fps   编码 {enc_name}')
    print(f'  输出   : {out}')
    print('  停止   : 运行 停录.bat，或在本窗口按 Ctrl+C')
    print()

    # CREATE_NEW_PROCESS_GROUP：让 Ctrl+C 只到 Python，不到 ffmpeg
    # （否则 ffmpeg 会直接被打断，收尾就白做了）
    flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=flags)

    stopped = False
    try:
        while proc.poll() is None:
            if os.path.exists(STOP_FILE):
                print('\n  收到停止信号，让 ffmpeg 收尾...')
                stopped = True
                break
            time.sleep(0.4)
        if stopped:
            try:
                proc.stdin.write(b'q')
                proc.stdin.flush()
            except Exception:
                pass
        proc.wait()
    except KeyboardInterrupt:
        print('\n  Ctrl+C，让 ffmpeg 收尾...')
        try:
            proc.stdin.write(b'q')
            proc.stdin.flush()
        except Exception:
            pass
        proc.wait()

    for p in (STOP_FILE,):
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    if os.path.exists(out):
        size = os.path.getsize(out)
        print(f'\n  录完: {out}  ({size/1024/1024:.1f} MB)')
        return 0
    print('\n  [x] 没产出文件')
    return 1


if __name__ == '__main__':
    sys.exit(main())
