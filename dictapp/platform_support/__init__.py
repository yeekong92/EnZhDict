"""Platform-specific integration: global hotkey, grabbing the selected text,
start-with-OS, and non-activating windows.

Everything OS-specific lives in this package. To port to macOS/Linux, add a
module implementing the same four functions + HotkeyListener and select it here.
(The package is not called ``platform`` to avoid shadowing the stdlib module.)
"""
from __future__ import annotations

import sys

from .hotkey import DoubleTapListener

if sys.platform == "win32":
    from .windows import copy_selection, is_start_with_os, make_noactivate, set_start_with_os
else:  # pragma: no cover — not implemented yet
    from .generic import copy_selection, is_start_with_os, make_noactivate, set_start_with_os

__all__ = ["DoubleTapListener", "copy_selection", "is_start_with_os", "set_start_with_os",
           "make_noactivate"]
