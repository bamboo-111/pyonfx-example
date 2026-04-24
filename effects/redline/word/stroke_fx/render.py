from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pyonfx import Ass, Utils

from .assets import parse_drawing, serialize_drawing
from .config import WordFxConfig, WordFxRenderOptions
from .models import GlyphAsset, PreparedCharRender, StrokeGroup
from .timing import (
    asset_total_bbox,
    build_char_timeline,
    build_stroke_groups,
    entry_vector_from_geometry,
    harmonize_line_timelines,
    stroke_windows,
    whole_char_vector,
)


def affine_transform_drawing(
    drawing: str,
    *,
    scale_x: float,
    scale_y: float,
    translate_x: float,
    translate_y: float,
) -> str:
    commands = parse_drawing(drawing)
    transformed: list[tuple[str, list[tuple[float, float]]]] = []
    for cmd, points in commands:
        transformed_points = [
            (x * scale_x + translate_x, y * scale_y + translate_y)
            for x, y in points
        ]
        transformed.append((cmd, transformed_points))
    return serialize_drawing(transformed)


def write_event(io: Ass, template_line: Any, *, layer: int, start_ms: int, end_ms: int, text: str) -> None:
    line = template_line.copy()
    line.layer = layer
    line.start_time = start_ms
    line.end_time = end_ms
    line.text = text
    io.write_line(line)


def _stroke_layer(options: WordFxRenderOptions) -> int:
    return options.layer_base + options.stroke_layer_offset


def _highlight_layer(options: WordFxRenderOptions) -> int:
    return options.layer_base + options.highlight_layer_offset


def _final_layer(options: WordFxRenderOptions) -> int:
    return options.layer_base + options.final_layer_offset


def emit_stroke_events(
    io: Ass,
    line: Any,
    line_index: int,
    char: Any,
    asset: GlyphAsset,
    timeline,
    stroke_groups: list[StrokeGroup],
    *,
    config: WordFxConfig,
    options: WordFxRenderOptions,
) -> None:
    windows = stroke_windows(timeline, len(stroke_groups), line_index, config)
    total_bbox = asset_total_bbox(asset, config.font_size)
    asset_width = max(1.0, total_bbox[2] - total_bbox[0])
    asset_height = max(1.0, total_bbox[3] - total_bbox[1])
    asset_center_x = (total_bbox[0] + total_bbox[2]) / 2.0
    asset_center_y = (total_bbox[1] + total_bbox[3]) / 2.0
    render_scale = min(char.width / asset_width, char.height / asset_height)

    travel_scale = config.line_travel_distance_scale[min(line_index, len(config.line_travel_distance_scale) - 1)]
    stroke_fade_in_ms = 45 if line_index == 0 else 0
    for stroke_group, (start_ms, end_ms) in zip(stroke_groups, windows, strict=True):
        dx, dy = entry_vector_from_geometry(
            stroke_group.bbox,
            origin_x=asset_center_x,
            origin_y=asset_center_y,
            stroke_index=stroke_group.indices[0],
            stroke_count=max(1, len(stroke_groups)),
            median_points=stroke_group.source_median,
        )
        local_drawing = affine_transform_drawing(
            stroke_group.ass_path,
            scale_x=render_scale,
            scale_y=render_scale,
            translate_x=-asset_center_x * render_scale,
            translate_y=-asset_center_y * render_scale,
        )
        travel_distance = stroke_group.entry_distance * max(char.width, char.height) * travel_scale
        start_x = char.center + dx * travel_distance
        start_y = char.middle + dy * travel_distance
        move_duration = max(1, end_ms - start_ms)
        tags = (
            f"\\an7\\move({start_x:.2f},{start_y:.2f},{char.center:.2f},{char.middle:.2f},0,{move_duration})"
            f"\\bord0\\shad0\\blur0.6\\1c&H2D2DCC&\\1a&H10&"
            + (f"\\alpha&HFF&\\t(0,{stroke_fade_in_ms},\\alpha&H10&)" if stroke_fade_in_ms > 0 else "")
            + f"\\p{config.draw_p_scale}"
        )
        write_event(
            io,
            line,
            layer=_stroke_layer(options),
            start_ms=start_ms,
            end_ms=timeline.highlight_start_ms,
            text=f"{{{tags}}}{local_drawing}{{\\p0}}",
        )


def emit_whole_char_event(
    io: Ass,
    line: Any,
    char: Any,
    timeline,
    *,
    config: WordFxConfig,
    options: WordFxRenderOptions,
) -> None:
    dx, dy = whole_char_vector(char.text)
    distance = config.font_size * 1.1
    start_x = char.center + dx * distance
    start_y = char.middle + dy * distance
    tags = (
        f"\\an5\\move({start_x:.2f},{start_y:.2f},{char.center:.2f},{char.middle:.2f})"
        f"\\fad(40,70)\\blur0.7\\bord1.2\\1c&HFFFFFF&\\3c&H2D2DCC&"
    )
    write_event(
        io,
        line,
        layer=_stroke_layer(options),
        start_ms=timeline.start_ms,
        end_ms=max(timeline.whole_char_end_ms, timeline.start_ms + 120),
        text=f"{{{tags}}}{char.text}",
    )


def emit_highlight_event(
    io: Ass,
    line: Any,
    char: Any,
    timeline,
    *,
    options: WordFxRenderOptions,
) -> None:
    duration = max(120, timeline.highlight_end_ms - timeline.highlight_start_ms)
    tags = (
        f"\\an5\\pos({char.center:.2f},{char.middle:.2f})\\1c&HFFFFFF&\\3c&HFFFFFF&"
        "\\alpha&HFF&\\bord4\\blur3"
        f"\\t(0,40,\\alpha&H20&)\\t(40,{duration},\\bord0\\blur0\\alpha&HFF&)"
    )
    write_event(
        io,
        line,
        layer=_highlight_layer(options),
        start_ms=timeline.highlight_start_ms,
        end_ms=timeline.highlight_end_ms,
        text=f"{{{tags}}}{char.text}",
    )


def emit_final_text_event(
    io: Ass,
    line: Any,
    char: Any,
    timeline,
    *,
    config: WordFxConfig,
    options: WordFxRenderOptions,
) -> None:
    char_time = getattr(char, "start_time", 0)
    if char_time < getattr(line, "start_time", 0):
        char_time += getattr(line, "start_time", 0)

    desired_fade_start = int(round(char_time + config.text_fade_offset_ms))
    event_start_ms = max(0, timeline.final_start_ms)
    fade_in = min(max(1, config.text_fade_in_ms), max(1, timeline.final_end_ms - event_start_ms))

    earliest_fade_start = event_start_ms + fade_in + max(0, config.text_min_visible_hold_ms)
    fade_start = min(
        max(earliest_fade_start, min(timeline.final_end_ms, desired_fade_start)),
        max(event_start_ms + fade_in + 1, timeline.final_end_ms - 1),
    )
    fade_end = min(timeline.final_end_ms, fade_start + max(1, config.text_fade_ms))
    fade_start_rel = max(0, fade_start - event_start_ms)
    fade_end_rel = max(fade_start_rel + 1, fade_end - event_start_ms)
    tags = (
        f"\\an5\\pos({char.center:.2f},{char.middle:.2f})\\alpha&HFF&"
        f"\\t(0,{fade_in},\\alpha&H00&)"
        f"\\t({fade_start_rel},{fade_end_rel},\\alpha&HFF&)"
    )
    write_event(
        io,
        line,
        layer=_final_layer(options),
        start_ms=event_start_ms,
        end_ms=timeline.final_end_ms,
        text=f"{{{tags}}}{char.text}",
    )


def prepare_line_chars(line: Any, line_index: int, assets: dict[str, GlyphAsset], config: WordFxConfig) -> list[PreparedCharRender]:
    chars = list(Utils.all_non_empty(line.chars, progress_bar=False))
    prepared: list[PreparedCharRender] = []
    for char_index, char in enumerate(chars):
        asset = assets[char.text]
        stroke_groups = build_stroke_groups(asset, config.font_size) if asset.mode == "full_stroke" and asset.strokes else []
        motion_units = len(stroke_groups) if stroke_groups else 1
        timeline = build_char_timeline(
            line.start_time,
            line.end_time,
            line_index,
            char_index,
            len(chars),
            asset,
            motion_units,
            config,
        )
        prepared.append(
            PreparedCharRender(
                char=char,
                asset=asset,
                stroke_groups=stroke_groups,
                timeline=timeline,
            )
        )
    return prepared


def render_word_effect(
    io: Ass,
    line: Any,
    line_index: int,
    assets: dict[str, GlyphAsset],
    *,
    config: WordFxConfig,
    options: WordFxRenderOptions | None = None,
) -> dict[str, Any]:
    options = options or WordFxRenderOptions()
    prepared_chars = prepare_line_chars(line, line_index, assets, config)
    harmonized = harmonize_line_timelines([item.timeline for item in prepared_chars], line.end_time, config)

    line_debug = {"text": line.text, "chars": []}
    for prepared, timeline in zip(prepared_chars, harmonized, strict=True):
        char = prepared.char
        asset = prepared.asset
        stroke_groups = prepared.stroke_groups
        motion_units = len(stroke_groups) if stroke_groups else 1
        if asset.mode == "full_stroke" and asset.strokes:
            emit_stroke_events(
                io,
                line,
                line_index,
                char,
                asset,
                timeline,
                stroke_groups,
                config=config,
                options=options,
            )
        else:
            emit_whole_char_event(io, line, char, timeline, config=config, options=options)
        emit_highlight_event(io, line, char, timeline, options=options)
        emit_final_text_event(
            io,
            line,
            char,
            timeline,
            config=config,
            options=options,
        )
        line_debug["chars"].append(
            {
                "char": char.text,
                "segmentation": "char_based",
                "mode": asset.mode,
                "source": asset.source,
                "stroke_count": asset.stroke_count,
                "motion_units": motion_units,
                "stroke_groups": [group.indices for group in stroke_groups],
                "timeline": asdict(timeline),
            }
        )
    return line_debug


def render_word_effect_for_lines(
    input_path: str,
    output_path: str,
    assets: dict[str, GlyphAsset],
    *,
    config: WordFxConfig,
    options: WordFxRenderOptions | None = None,
    keep_original: bool = False,
    extended: bool = True,
) -> list[dict[str, Any]]:
    io = Ass(input_path, output_path, keep_original=keep_original, extended=extended)
    _, _, lines = io.get_data()
    debug_lines = [
        render_word_effect(io, line, line_index, assets, config=config, options=options)
        for line_index, line in enumerate(lines)
    ]
    io.save(quiet=True)
    return debug_lines
