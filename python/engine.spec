# PyInstaller spec for the Flowsurface data engine.
#
# Build:
#   pyinstaller --clean --noconfirm python/engine.spec
#
# Output:
#   dist/flowsurface-engine[.exe]
#
# This spec produces a single-file executable (`onefile=True`) that bundles
# the `engine` package together with the CPython runtime.  The Rust viewer
# locates this binary next to its own executable (see `EngineCommand::resolve`
# in `engine-client/src/process.rs`).
#
# Notes:
# - `console=True` keeps stderr/stdout open so the Rust process can pipe them
#   into the `engine` log target (Phase 6, spec §6.4).
# - `hiddenimports` lists every exchange module: PyInstaller cannot detect
#   them via static analysis because `DataEngineServer.__init__` references
#   them through dict-style dispatch.

block_cipher = None


a = Analysis(
    ["engine/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[
        "engine.exchanges.binance",
        "engine.exchanges.bybit",
        "engine.exchanges.hyperliquid",
        "engine.exchanges.okex",
        "engine.exchanges.mexc",
        "engine.limiter",
        "engine.schemas",
        "engine.server",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Test-only; keep the binary small.
        "pytest",
        "pytest_asyncio",
        "pytest_httpx",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="flowsurface-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
