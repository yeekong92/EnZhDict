"""Render the app icon to resources/icon.png and resources/icon.ico."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication(sys.argv)
from dictapp.app import draw_icon_pixmap  # noqa: E402

out = ROOT / "resources"
out.mkdir(exist_ok=True)
draw_icon_pixmap(256).save(str(out / "icon.png"))
ok = draw_icon_pixmap(256).save(str(out / "icon.ico"))
print("icon.png + icon.ico written" if ok else "icon.png written (ICO writer unavailable)")
