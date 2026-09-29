"""EnZhDict — English ⇄ Chinese desktop dictionary.

    python main.py          open the main window (and tray icon)
    python main.py --tray   start minimised to the tray
"""
import sys

from dictapp.app import run

if __name__ == "__main__":
    sys.exit(run(sys.argv))
