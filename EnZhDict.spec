# PyInstaller spec — one-folder build.   Run:  pyinstaller EnZhDict.spec
# Output: dist/EnZhDict/EnZhDict.exe
from pathlib import Path

root = Path(SPECPATH)
datas = [(str(root / "resources" / "dict.db"), "resources")]
for name in ("icon.png", "icon.ico"):
    if (root / "resources" / name).exists():
        datas.append((str(root / "resources" / name), "resources"))

a = Analysis(
    ["main.py"],
    pathex=[str(root)],
    datas=datas,
    hiddenimports=[
        "pynput.keyboard._win32", "pynput.mouse._win32",   # chosen at runtime by pynput
        "PySide6.QtTextToSpeech",
    ],
    excludes=[
        "tkinter", "unittest", "pytest", "argostranslate", "torch",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtPdf", "PySide6.QtBluetooth", "PySide6.QtSql", "PySide6.QtTest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="EnZhDict",
    console=False,
    icon=str(root / "resources" / "icon.ico") if (root / "resources" / "icon.ico").exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="EnZhDict")
