from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pyonfx import Ass, Utils

from .config import WordFxConfig, WordFxPaths
from .models import GlyphAsset
from .render import affine_transform_drawing
from .timing import asset_total_bbox


def write_debug_json(
    paths: WordFxPaths,
    config: WordFxConfig,
    lines: list[str],
    assets: dict[str, GlyphAsset],
    line_debug: list[dict[str, Any]],
) -> None:
    payload = {
        "font_name": config.font_name,
        "font_size": config.font_size,
        "draw_p_scale": config.draw_p_scale,
        "lines": lines,
        "assets": {char: asdict(asset) for char, asset in assets.items()},
        "render": line_debug,
    }
    paths.test_debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_reference_char_metrics(
    input_path: Path,
    probe_output_path: Path,
    reference_char: str,
    font_size: int,
) -> tuple[float, float]:
    io = Ass(str(input_path), str(probe_output_path), keep_original=False, extended=True)
    _, _, lines = io.get_data()
    for line in lines:
        chars = list(Utils.all_non_empty(line.chars, progress_bar=False))
        for char in chars:
            if char.text == reference_char:
                return float(char.width), float(char.height)
    return float(font_size), float(font_size)


def write_static_glyph_diagnostic(
    paths: WordFxPaths,
    config: WordFxConfig,
    asset: GlyphAsset,
    diagnostic_char: str,
) -> None:
    total_bbox = asset_total_bbox(asset, config.font_size)
    asset_width = max(1.0, total_bbox[2] - total_bbox[0])
    asset_height = max(1.0, total_bbox[3] - total_bbox[1])
    center_x = config.play_res_x / 2
    center_y = config.play_res_y / 2
    ref_width, ref_height = get_reference_char_metrics(
        paths.test_input_path,
        paths.root / "_diag_metrics.ass",
        diagnostic_char,
        config.font_size,
    )
    render_scale = min(ref_width / asset_width, ref_height / asset_height)
    asset_center_x = (total_bbox[0] + total_bbox[2]) / 2.0
    asset_center_y = (total_bbox[1] + total_bbox[3]) / 2.0

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {config.play_res_x}",
        f"PlayResY: {config.play_res_y}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{config.font_name},{config.font_size},&H00FFFFFF,&H000000FF,&H002A1A32,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,60,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for stroke in asset.strokes:
        local_drawing = affine_transform_drawing(
            stroke.ass_path,
            scale_x=render_scale,
            scale_y=render_scale,
            translate_x=-asset_center_x * render_scale,
            translate_y=-asset_center_y * render_scale,
        )
        color_cycle = ["&H2D2DCC&", "&H1C72D1&", "&H1E9C7C&", "&H2B77E5&", "&H3A3AE0&"]
        color = color_cycle[stroke.index % len(color_cycle)]
        lines.append(
            "Dialogue: 10,0:00:00.00,0:00:10.00,Default,,0000,0000,0000,,"
            f"{{\\an7\\pos({center_x:.2f},{center_y:.2f})\\bord0\\shad0\\blur0.6\\1c{color}\\1a&H00&\\p{config.draw_p_scale}}}"
            f"{local_drawing}{{\\p0}}"
        )
        label_x = center_x + (stroke.bbox[0] + stroke.bbox[2] - 2 * asset_center_x) * render_scale * 0.5
        label_y = center_y + (stroke.bbox[1] + stroke.bbox[3] - 2 * asset_center_y) * render_scale * 0.5
        lines.append(
            "Dialogue: 20,0:00:00.00,0:00:10.00,Default,,0000,0000,0000,,"
            f"{{\\an5\\pos({label_x:.2f},{label_y:.2f})\\fs20\\bord1\\shad0\\1c&HFFFFFF&\\3c&H000000&}}{stroke.index + 1}"
        )

    lines.append(
        "Dialogue: 30,0:00:00.00,0:00:10.00,Default,,0000,0000,0000,,"
        f"{{\\an5\\pos({center_x:.2f},{center_y:.2f})\\1c&HFFFFFF&\\3c&H202020&\\bord1.5\\shad0}}{diagnostic_char}"
    )
    lines.append(
        "Dialogue: 40,0:00:00.00,0:00:10.00,Default,,0000,0000,0000,,"
        f"{{\\an5\\pos({center_x:.2f},{center_y + 140:.2f})\\fs28\\bord0\\shad0}}Static stroke diagnostic: {diagnostic_char} / {asset.source} / strokes={asset.stroke_count} / ref={ref_width:.1f}x{ref_height:.1f}"
    )
    paths.static_diagnostic_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
