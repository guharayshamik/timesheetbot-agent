# timesheetbot_agent/errors.py
from __future__ import annotations

import os
import sys
import traceback
from functools import wraps
from datetime import datetime
from .ui import UserCancelled

from .ui import panel

LOG_DIR = os.path.join(os.path.expanduser("~"), ".tsbot")
LOG_PATH = os.path.join(LOG_DIR, "tsbot_errors.log")

def _log_error(e: BaseException) -> str:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}] {type(e).__name__}: {e}\n")
            traceback.print_exc(file=f)
        return LOG_PATH
    except Exception:
        return ""

def catch_all(*, flow: str = "App", on_cancel: str = "stay"):
    """
    Decorator to make top-level loops resilient.
    - on_cancel: "stay" -> show Cancelled panel and return to the loop.
                 "exit" -> show Cancelled panel and exit the program.
    """
    assert on_cancel in ("stay", "exit")
    def _decorator(fn):
        @wraps(fn)
        def _wrapped(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (KeyboardInterrupt, EOFError):
                panel("↩️ Cancelled.")
                if on_cancel == "exit":
                    # exit entire tool
                    sys.exit(0)
                # else: just return to caller (stay in current flow/menu)
                return
            except UserCancelled:
                panel("↩️ Cancelled.")
                if on_cancel == "exit":
                    sys.exit(0)
                return
            except SystemExit:
                raise
            except Exception as e:
                path = _log_error(e)
                panel(f"↩️ Back to Main Menu")
                return
        return _wrapped
    return _decorator
