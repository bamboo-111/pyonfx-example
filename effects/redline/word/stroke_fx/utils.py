from __future__ import annotations

import math
import re


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")
DRAWING_RE = re.compile(r"\\p(?P<scale>\d+)\}(?P<drawing>.*?)(?:\{\\p0\}|$)")


def strip_tags(text: str) -> str:
    return re.sub(r"\{[^{}]*\}", "", text)


def is_kana(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3040 <= codepoint <= 0x309F
        or 0x30A0 <= codepoint <= 0x30FF
        or 0x31F0 <= codepoint <= 0x31FF
        or 0xFF66 <= codepoint <= 0xFF9F
    )


def is_han(char: str) -> bool:
    codepoint = ord(char)
    return 0x4E00 <= codepoint <= 0x9FFF or 0x3400 <= codepoint <= 0x4DBF


def is_drawable_char(char: str) -> bool:
    return not char.isspace()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def normalize_vector(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return (0.0, -1.0)
    return (dx / length, dy / length)


def ass_timestamp(ms: int) -> str:
    total_cs = round(ms / 10)
    cs = total_cs % 100
    total_seconds = total_cs // 100
    seconds = total_seconds % 60
    total_minutes = total_seconds // 60
    minutes = total_minutes % 60
    hours = total_minutes // 60
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"


def unicode_tag(char: str) -> str:
    return f"U+{ord(char):04X}"


def codepoint_decimal(char: str) -> str:
    return str(ord(char))


def codepoint_hex5(char: str) -> str:
    return f"{ord(char):05x}"
