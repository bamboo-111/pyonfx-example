from __future__ import annotations

import math

from .config import WordFxConfig
from .models import CharTimeline, GlyphAsset, StrokeAsset, StrokeGroup
from .utils import clamp, normalize_vector


def whole_char_vector(char: str) -> tuple[float, float]:
    angle = (ord(char) % 360) * math.pi / 180.0
    dx, dy = normalize_vector(math.cos(angle), -abs(math.sin(angle)) - 0.3)
    return dx, dy


def asset_total_bbox(asset: GlyphAsset, font_size: int) -> list[float]:
    if not asset.strokes:
        return [0.0, 0.0, float(font_size), float(font_size)]
    min_x = min(stroke.bbox[0] for stroke in asset.strokes)
    min_y = min(stroke.bbox[1] for stroke in asset.strokes)
    max_x = max(stroke.bbox[2] for stroke in asset.strokes)
    max_y = max(stroke.bbox[3] for stroke in asset.strokes)
    return [min_x, min_y, max_x, max_y]


def bbox_center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def interval_overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    base = max(1e-6, min(a1 - a0, b1 - b0))
    return overlap / base


def merge_stroke_group(strokes: list[StrokeAsset]) -> StrokeGroup:
    bbox = [
        min(stroke.bbox[0] for stroke in strokes),
        min(stroke.bbox[1] for stroke in strokes),
        max(stroke.bbox[2] for stroke in strokes),
        max(stroke.bbox[3] for stroke in strokes),
    ]
    medians: list[list[float]] = []
    for stroke in strokes:
        medians.extend(stroke.source_median)
    return StrokeGroup(
        indices=[stroke.index for stroke in strokes],
        ass_path=" ".join(stroke.ass_path for stroke in strokes),
        bbox=bbox,
        source_median=medians,
        entry_distance=max(stroke.entry_distance for stroke in strokes),
    )


def should_merge_strokes(
    previous: StrokeAsset,
    current: StrokeAsset,
    *,
    asset_width: float,
    asset_height: float,
    current_group_size: int,
) -> bool:
    if current_group_size >= 4:
        return False
    prev_center = bbox_center(previous.bbox)
    curr_center = bbox_center(current.bbox)
    asset_diag = max(1.0, math.hypot(asset_width, asset_height))
    center_distance = math.hypot(curr_center[0] - prev_center[0], curr_center[1] - prev_center[1]) / asset_diag
    overlap_x = interval_overlap_ratio(previous.bbox[0], previous.bbox[2], current.bbox[0], current.bbox[2])
    overlap_y = interval_overlap_ratio(previous.bbox[1], previous.bbox[3], current.bbox[1], current.bbox[3])
    prev_tail = previous.source_median[-1] if previous.source_median else [prev_center[0], prev_center[1]]
    curr_head = current.source_median[0] if current.source_median else [curr_center[0], curr_center[1]]
    continuity = math.hypot(curr_head[0] - prev_tail[0], curr_head[1] - prev_tail[1]) / asset_diag
    same_band = overlap_x >= 0.22 or overlap_y >= 0.22
    tightly_connected = continuity <= 0.24 or center_distance <= 0.16
    return tightly_connected and (same_band or continuity <= 0.18)


def build_stroke_groups(asset: GlyphAsset, font_size: int) -> list[StrokeGroup]:
    if asset.mode != "full_stroke" or not asset.strokes:
        return []
    if asset.stroke_count <= 5:
        return [merge_stroke_group([stroke]) for stroke in asset.strokes]

    total_bbox = asset_total_bbox(asset, font_size)
    asset_width = max(1.0, total_bbox[2] - total_bbox[0])
    asset_height = max(1.0, total_bbox[3] - total_bbox[1])
    groups: list[list[StrokeAsset]] = [[asset.strokes[0]]]
    for stroke in asset.strokes[1:]:
        current_group = groups[-1]
        if should_merge_strokes(
            current_group[-1],
            stroke,
            asset_width=asset_width,
            asset_height=asset_height,
            current_group_size=len(current_group),
        ):
            current_group.append(stroke)
        else:
            groups.append([stroke])
    return [merge_stroke_group(group) for group in groups]


def build_char_timeline(
    line_start: int,
    line_end: int,
    line_index: int,
    char_index: int,
    char_count: int,
    asset: GlyphAsset,
    motion_units: int,
    config: WordFxConfig,
) -> CharTimeline:
    line_duration = line_end - line_start
    effective_units = max(1, motion_units)
    timing_scale = config.line_stroke_timing_scale[min(line_index, len(config.line_stroke_timing_scale) - 1)]
    base_animation_ms = int(max(180, effective_units * 48 + 80) * timing_scale)
    available_span = max(600, int(line_duration * 0.8))
    max_step = available_span if char_count <= 1 else max(config.char_entry_step_ms, available_span // char_count)
    step = min(config.char_entry_step_ms, max_step)
    line_entry_start = max(0, line_start - config.line_entry_lead_in_ms)
    char_start = min(line_end - 280, line_entry_start + char_index * step)

    if asset.mode == "full_stroke":
        stroke_total = max(config.min_stroke_ms * effective_units, min(base_animation_ms, 840))
        stroke_end = char_start + stroke_total
        whole_char_end = char_start
    else:
        stroke_end = char_start
        whole_char_end = min(line_end - 180, char_start + config.whole_char_move_ms)

    highlight_start = (
        min(line_end - 120, stroke_end + config.assembly_hold_ms)
        if asset.mode == "full_stroke"
        else whole_char_end
    )
    highlight_end = min(line_end, highlight_start + config.highlight_ms)
    final_start = min(line_end - 120, highlight_start + 20)
    final_end = line_end

    return CharTimeline(
        start_ms=char_start,
        stroke_start_ms=char_start,
        stroke_end_ms=stroke_end,
        highlight_start_ms=highlight_start,
        highlight_end_ms=highlight_end,
        final_start_ms=final_start,
        final_end_ms=final_end,
        whole_char_end_ms=whole_char_end,
    )


def harmonize_line_timelines(
    timelines: list[CharTimeline],
    line_end: int,
    config: WordFxConfig,
) -> list[CharTimeline]:
    if not timelines:
        return timelines
    adjusted = [timelines[0]]
    for timeline in timelines[1:]:
        previous = adjusted[-1]
        highlight_start = timeline.highlight_start_ms
        if highlight_start < previous.highlight_start_ms:
            highlight_start = previous.highlight_start_ms
        highlight_end = min(line_end, highlight_start + config.highlight_ms)
        final_start = min(line_end - 120, highlight_start + 20)
        adjusted.append(
            CharTimeline(
                start_ms=timeline.start_ms,
                stroke_start_ms=timeline.stroke_start_ms,
                stroke_end_ms=timeline.stroke_end_ms,
                highlight_start_ms=highlight_start,
                highlight_end_ms=highlight_end,
                final_start_ms=final_start,
                final_end_ms=timeline.final_end_ms,
                whole_char_end_ms=timeline.whole_char_end_ms,
            )
        )
    return adjusted


def stroke_windows(
    timeline: CharTimeline,
    motion_units: int,
    line_index: int,
    config: WordFxConfig,
) -> list[tuple[int, int]]:
    if motion_units <= 0:
        return []
    total = timeline.stroke_end_ms - timeline.stroke_start_ms
    if total <= 0:
        return []
    base_overlap = config.line_stroke_overlap_ratio[min(line_index, len(config.line_stroke_overlap_ratio) - 1)]
    overlap_boost = clamp((motion_units - 4) * 0.06, 0.0, 0.28)
    overlap_ratio = clamp(base_overlap + overlap_boost, 0.0, 0.72)
    nominal_duration = max(
        config.min_stroke_ms,
        int(total / max(1.0, 1.0 + (motion_units - 1) * (1.0 - overlap_ratio))),
    )
    durations = [nominal_duration for _ in range(motion_units)]
    result: list[tuple[int, int]] = []
    start = timeline.stroke_start_ms
    for index, duration in enumerate(durations):
        end = min(timeline.stroke_end_ms, start + duration)
        result.append((start, end))
        if index < motion_units - 1:
            advance = max(24, int(duration * (1.0 - overlap_ratio)))
            start = min(timeline.stroke_end_ms - config.min_stroke_ms, start + advance)
    return result


def entry_vector_from_geometry(
    bbox: list[float],
    *,
    origin_x: float,
    origin_y: float,
    stroke_index: int,
    stroke_count: int,
    median_points: list[list[float]],
) -> list[float]:
    center_x = (bbox[0] + bbox[2]) / 2.0
    center_y = (bbox[1] + bbox[3]) / 2.0
    rel_x = center_x - origin_x
    rel_y = center_y - origin_y

    if abs(rel_x) < 4 and abs(rel_y) < 4:
        geo_dx, geo_dy = (0.0, -1.0)
    else:
        geo_dx, geo_dy = normalize_vector(rel_x, rel_y)

    ring_angle = ((stroke_index + 0.5) / max(1, stroke_count)) * math.tau
    ring_dx = math.cos(ring_angle)
    ring_dy = math.sin(ring_angle)
    base_dx, base_dy = normalize_vector(ring_dx * 0.75 + geo_dx * 0.25, ring_dy * 0.75 + geo_dy * 0.25)

    if len(median_points) >= 2:
        first = median_points[0]
        second = median_points[1]
        med_dx, med_dy = normalize_vector(first[0] - second[0], first[1] - second[1])
        blended_dx, blended_dy = normalize_vector(base_dx * 0.92 + med_dx * 0.08, base_dy * 0.92 + med_dy * 0.08)
        return [round(blended_dx, 4), round(blended_dy, 4)]

    return [round(base_dx, 4), round(base_dy, 4)]
