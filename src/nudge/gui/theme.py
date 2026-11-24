"""Colours and type for the desktop app: a quiet, dark-first look with one accent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str  # window
    surface: str  # input well, editor
    raised: str  # hover, selected tab
    line: str  # hairlines
    text: str
    dim: str
    faint: str
    accent: str
    green: str
    amber: str
    red: str
    sky: str

    def tone(self, name: str) -> str:
        return getattr(self, name)


DARK = Palette(
    name="dark",
    bg="#161618",
    surface="#1D1D20",
    raised="#26262A",
    line="#2B2B30",
    text="#ECECEE",
    dim="#8E8E95",
    faint="#55555C",
    accent="#8AA4FF",
    green="#5BD69A",
    amber="#F5B961",
    red="#FF6B6B",
    sky="#6CC6FF",
)

LIGHT = Palette(
    name="light",
    bg="#FAFAFA",
    surface="#F1F1F3",
    raised="#EAEAED",
    line="#E3E3E7",
    text="#1C1C1E",
    dim="#6E6E76",
    faint="#A6A6AD",
    accent="#3B5BDB",
    green="#1F9D5C",
    amber="#C27A00",
    red="#E5484D",
    sky="#0A7CD8",
)

#: Traffic-light window buttons.
LIGHTS = {"close": "#FF5F57", "min": "#FEBC2E", "zoom": "#28C840"}

#: Font families registered from ``assets/fonts`` (paths are relative to the assets folder).
FONTS = {
    "Inter": "fonts/Inter-Regular.ttf",
    "Inter Medium": "fonts/Inter-Medium.ttf",
    "Inter SemiBold": "fonts/Inter-SemiBold.ttf",
    "Mono": "fonts/JetBrainsMono-Regular.ttf",
    "Mono Medium": "fonts/JetBrainsMono-Medium.ttf",
}


def pick(setting: str, system_is_dark: bool | None) -> Palette:
    if setting == "light":
        return LIGHT
    if setting == "dark":
        return DARK
    return LIGHT if system_is_dark is False else DARK
