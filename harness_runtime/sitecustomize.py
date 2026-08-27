"""Keep agent-authored Python file operations inside the run workspace."""

from __future__ import annotations

import builtins
import io
import os
from pathlib import Path
import re
from typing import Any, Callable


_WORKSPACE_VALUE = os.environ.get("DEEPAGENT_WORKSPACE_ROOT")
_WORKSPACE = Path(_WORKSPACE_VALUE).resolve() if _WORKSPACE_VALUE else None
_VIRTUAL_PATH = re.compile(
    r"^(?:[A-Za-z]:)?[/\\](inputs|outputs|skills)(?:[/\\](.*))?$",
    re.IGNORECASE,
)


def _mapped_path(value: Any) -> Any:
    if _WORKSPACE_VALUE is None or isinstance(value, int):
        return value

    try:
        raw = os.fspath(value)
    except TypeError:
        return value

    was_bytes = isinstance(raw, bytes)
    text = os.fsdecode(raw)
    match = _VIRTUAL_PATH.fullmatch(text)
    if match is None:
        return value

    assert _WORKSPACE is not None
    root_name = match.group(1).lower()
    remainder = match.group(2) or ""
    components = [part for part in re.split(r"[/\\]+", remainder) if part]
    target = (_WORKSPACE / root_name).joinpath(*components).resolve()
    try:
        target.relative_to(_WORKSPACE)
    except ValueError as exc:
        raise PermissionError(
            f"Virtual path escapes the agent workspace: {text}"
        ) from exc

    mapped = str(target)
    return os.fsencode(mapped) if was_bytes else mapped


def _wrap_single_path(function: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(path: Any, *args: Any, **kwargs: Any) -> Any:
        return function(_mapped_path(path), *args, **kwargs)

    return wrapped


def _wrap_optional_path(function: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(path: Any = ".", *args: Any, **kwargs: Any) -> Any:
        return function(_mapped_path(path), *args, **kwargs)

    return wrapped


def _wrap_two_paths(function: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(source: Any, target: Any, *args: Any, **kwargs: Any) -> Any:
        return function(
            _mapped_path(source),
            _mapped_path(target),
            *args,
            **kwargs,
        )

    return wrapped


if _WORKSPACE_VALUE:
    builtins.open = _wrap_single_path(builtins.open)
    io.open = _wrap_single_path(io.open)

    for _name in (
        "access",
        "chdir",
        "chmod",
        "lstat",
        "mkdir",
        "open",
        "readlink",
        "remove",
        "rmdir",
        "stat",
        "unlink",
    ):
        if hasattr(os, _name):
            setattr(os, _name, _wrap_single_path(getattr(os, _name)))

    for _name in ("listdir", "scandir"):
        if hasattr(os, _name):
            setattr(os, _name, _wrap_optional_path(getattr(os, _name)))

    for _name in ("rename", "replace"):
        if hasattr(os, _name):
            setattr(os, _name, _wrap_two_paths(getattr(os, _name)))

    for _name in (
        "abspath",
        "exists",
        "getatime",
        "getctime",
        "getmtime",
        "getsize",
        "isdir",
        "isfile",
        "islink",
        "lexists",
        "realpath",
    ):
        if hasattr(os.path, _name):
            setattr(
                os.path,
                _name,
                _wrap_single_path(getattr(os.path, _name)),
            )

    if hasattr(os.path, "samefile"):
        os.path.samefile = _wrap_two_paths(os.path.samefile)
