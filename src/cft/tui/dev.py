from __future__ import annotations

from cft.bootstrap import build_application
from cft.tui.app import CftApp

app = CftApp(application=build_application())
