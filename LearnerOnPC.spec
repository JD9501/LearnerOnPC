# PyInstaller spec for LearnerOnPC (build on Windows):  python -m PyInstaller --noconfirm LearnerOnPC.spec
from PyInstaller.utils.hooks import collect_data_files

datas = [("assets", "assets"), ("demo_sessions", "demo_sessions"), ("THIRD_PARTY_NOTICES.md", ".")]
for pkg in ("huggingface_hub", "certifi"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

a = Analysis(
    ["main.py"], pathex=["."], binaries=[], datas=datas,
    hiddenimports=["sqlite3", "truststore", "psutil", "tqdm.auto"],
    excludes=["tkinter", "matplotlib", "pandas", "numpy", "scipy", "IPython", "pytest", "torch", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LearnerOnPC", console=False, icon="assets/icon.ico")
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LearnerOnPC")
