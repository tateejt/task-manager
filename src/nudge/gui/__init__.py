"""Nudge desktop app. ``nudge-app`` (or ``nudge`` with no arguments on Windows) opens it."""

from __future__ import annotations

import sys


def available() -> bool:
    try:
        import flet  # noqa: F401
    except ImportError:
        return False
    return True


def main() -> None:
    """Entry point for the windowed ``nudge-app`` launcher."""
    from .app import run

    run(minimized="--minimized" in sys.argv)
