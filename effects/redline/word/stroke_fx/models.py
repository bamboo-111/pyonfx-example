from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StrokeAsset:
    index: int
    ass_path: str
    bbox: list[float]
    entry_vector: list[float]
    entry_distance: float
    source_median: list[list[float]]


@dataclass(slots=True)
class GlyphAsset:
    char: str
    unicode: str
    source: str
    mode: str
    font_family: str
    stroke_count: int
    view_box: list[float]
    strokes: list[StrokeAsset]
    debug: dict[str, Any]


@dataclass(slots=True)
class CharTimeline:
    start_ms: int
    stroke_start_ms: int
    stroke_end_ms: int
    highlight_start_ms: int
    highlight_end_ms: int
    final_start_ms: int
    final_end_ms: int
    whole_char_end_ms: int


@dataclass(slots=True)
class StrokeGroup:
    indices: list[int]
    ass_path: str
    bbox: list[float]
    source_median: list[list[float]]
    entry_distance: float


@dataclass(slots=True)
class PreparedCharRender:
    char: Any
    asset: GlyphAsset
    stroke_groups: list[StrokeGroup]
    timeline: CharTimeline
