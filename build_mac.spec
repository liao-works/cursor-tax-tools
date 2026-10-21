# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['tariff_gui.py'],  # 你的主程序文件
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'), ('tariffs.db', '.')],  # 添加需要的资源文件
    hiddenimports=['backoff', 'Levenshtein'],  # 添加缺失的模块
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='uk-tax-tools',  # 应用程序名称
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 如果是GUI应用，设置为False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='uk-tax-tools'  # 应用程序名称
)