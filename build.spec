# -*- mode: python -*-
# PyInstaller spec for Conjure Crosshair

block_cipher = None

added_files = [
    ('assets/cross.png', 'assets'),
    ('assets/carrot.png', 'assets'),
    ('assets/dot.png', 'assets'),
    ('icon.ico', '.'),
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=added_files,
    hiddenimports=['PyQt6', 'pystray', 'keyboard', 'mouse', 'PIL'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='Conjure Crosshair',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    onefile=True,
    icon='icon.ico',
)
