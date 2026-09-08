"""Structured error codes for window operations.

Why a code and not just a message
---------------------------------
The rest of this repository reports failures as a free-text `error` string,
which is fine for a human reading a log and useless for a caller that has to
decide what to do next. "window 1234 not found" and "the window is still open"
call for different handling, and telling them apart by matching on English
prose is the kind of coupling that breaks the first time a message is reworded.

So `error_code` is added **alongside** `error`, never instead of it: existing
callers that read the string keep working, and new ones can branch on the code.

Codes are added here rather than inline so the same string cannot drift between
the three backends — the same reason the allowlist, the geometry validator and
the input validator each live in one module.
"""
from __future__ import annotations

from typing import Any

# The handle does not name a window: it never did, or the window is gone —
# including "gone while this call was running", which is the case that made the
# old code report success. `wmctrl -i -c <dead window>` exits **0**.
WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"

# The close was requested and accepted, and the window is still there. An
# application is entitled to refuse: an unsaved document puts up "save changes?"
# and waits. That is not a failure of the request, but it is emphatically not
# the window being closed, and the caller must be able to tell.
WINDOW_STILL_OPEN = "WINDOW_STILL_OPEN"

# The activate ran without error and another window holds the focus. A window
# manager may refuse focus (focus-stealing prevention), and a caller that
# assumed otherwise would type into the wrong place.
FOCUS_NOT_GRANTED = "FOCUS_NOT_GRANTED"

# No tool or binding to act through — a different answer from "the operation
# failed", and one an operator fixes in their package list, not their code.
TOOL_UNAVAILABLE = "TOOL_UNAVAILABLE"


def failure(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """A refusal carrying both the code and the human-readable reason."""
    return {"ok": False, "error_code": code, "error": message, **extra}
