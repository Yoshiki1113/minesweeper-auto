# -*- mode: python ; coding: utf-8 -*-
"""调试版打包（带控制台窗口，能看到 print 输出）。

⚠️ 必须把**扫雷整个目录**打进去 —— Minesweeper.exe 单独一个是起不来的，
   它还要 Minesweeper.dll / slc.dll / zh-CN\\Minesweeper.exe.mui。
   这几个文件从 **spec 所在目录**读（不再写死某个用户名下的绝对路径）；
   它们不入库（见 .gitignore），打包前需自行放到 spec 旁边。
"""
import os
from PyInstaller.utils.hooks import collect_all

_here = globals().get('SPECPATH') or os.getcwd()

# 扫雷本体：4 个文件缺一不可
_GAME = [
    ('Minesweeper.exe', '.'),
    ('Minesweeper.dll', '.'),
    ('slc.dll', '.'),
    ('zh-CN', 'zh-CN'),
]

datas = []
_missing = [src for src, _ in _GAME if not os.path.exists(os.path.join(_here, src))]
if _missing:
    raise SystemExit(
        '[打包中止] 缺少扫雷文件: %s\n'
        '请把它们放到 %s 下再打包（这些文件不入库，见 .gitignore）。'
        % (', '.join(_missing), _here)
    )
for _src, _dst in _GAME:
    datas.append((os.path.join(_here, _src), _dst))

binaries = []
hiddenimports = ['comtypes.gen.UIAutomationClient']
tmp_ret = collect_all('comtypes')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='扫雷AIdbg',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
