# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)
from src.version import VERSION_FILE, resolve_git_version

# Release builds can use the triggering tag even if multiple tags point at HEAD.
release_tag = os.environ.get("GITHUB_REF_NAME") if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
version = release_tag or resolve_git_version(Path(SPECPATH))
version_file = Path(workpath) / VERSION_FILE
version_file.write_text(version or "", encoding="utf-8")

a = Analysis(
    ["src/main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("src/templates", "templates"),
        ("src/static", "static"),
        ("src/settings.yaml.example", "."),
        (str(version_file), "."),
    ],
    hiddenimports=[
        "flask_limiter",
        "flask_login",
        "flask_wtf",
        "psutil",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="palworld-monitor",
    icon="assets/cattiva.ico" if sys.platform == "win32" else None,
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
