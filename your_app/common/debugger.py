# your_app/common/debugger.py — NO-OP debugger (no files, no side effects)
from __future__ import annotations

def start_run(context: dict) -> str | None:
    return None

def log(event: str, **data):
    return None

def finish_run(**summary):
    return None
