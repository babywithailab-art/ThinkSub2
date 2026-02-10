# -*- mode: python ; coding: utf-8 -*-


from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("faster_whisper")
hiddenimports = collect_submodules("faster_whisper")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'nvidia', 'triton'],
    noarchive=False,
    optimize=0,
)

# Filter out ONLY torch-specific binaries
# Allow cudnn, cublas, libiomp as ctranslate2 might need them
exclude_patterns = [
    'torch',
    'c10',
    'cufft',
    'nvJitLink',
    'curand',
    'cusolverMg',
    'fbgemm',
    'asmjit',
    'uv.dll'
]

a.binaries = TOC([x for x in a.binaries if not any(pattern in x[0] for pattern in exclude_patterns)])
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ThinkSub2',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ThinkSub2',
)
