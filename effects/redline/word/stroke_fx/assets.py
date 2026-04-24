from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from .config import WordFxConfig, WordFxPaths
from .models import GlyphAsset, StrokeAsset
from .utils import (
    DRAWING_RE,
    NUMBER_RE,
    SVG_NS,
    codepoint_decimal,
    codepoint_hex5,
    is_drawable_char,
    is_han,
    is_kana,
    unicode_tag,
)


def ensure_dirs(paths: WordFxPaths) -> None:
    for path in (paths.download_dir, paths.glyph_asset_dir, paths.temp_svg_dir):
        path.mkdir(parents=True, exist_ok=True)


def download_text(url: str, cache_path: Path, timeout: int) -> str | None:
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    response = requests.get(url, timeout=timeout)
    if response.status_code != 200:
        return None
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(response.text, encoding="utf-8")
    return response.text


def extract_view_box(root: ET.Element) -> list[float]:
    view_box = root.attrib.get("viewBox", "0 0 1024 1024").replace(",", " ")
    values = [float(value) for value in view_box.split()]
    if len(values) != 4:
        return [0.0, 0.0, 1024.0, 1024.0]
    return values


def median_points_from_path(path_d: str) -> list[list[float]]:
    numbers = [float(value) for value in NUMBER_RE.findall(path_d)]
    points: list[list[float]] = []
    for index in range(0, len(numbers) - 1, 2):
        points.append([numbers[index], numbers[index + 1]])
    return points


def parse_animcjk_strokes(svg_text: str, char: str) -> tuple[list[tuple[str, list[list[float]]]], list[float]]:
    root = ET.fromstring(svg_text)
    all_paths = root.findall(".//svg:path", SVG_NS)
    view_box = extract_view_box(root)
    decimal_code = codepoint_decimal(char)
    stroke_paths = [node for node in all_paths if node.attrib.get("id", "").startswith(f"z{decimal_code}d")]
    median_paths = [node for node in all_paths if node.attrib.get("id") is None and "clip-path" in node.attrib]
    medians = [median_points_from_path(node.attrib.get("d", "")) for node in median_paths]
    strokes: list[tuple[str, list[list[float]]]] = []
    for index, node in enumerate(stroke_paths):
        median = medians[index] if index < len(medians) else []
        strokes.append((node.attrib["d"], median))
    return strokes, view_box


def parse_kanjivg_paths(svg_text: str) -> tuple[list[str], list[float]]:
    root = ET.fromstring(svg_text)
    paths = root.findall(".//svg:path", SVG_NS)
    view_box = extract_view_box(root)
    return [node.attrib["d"] for node in paths if node.attrib.get("id", "").startswith("kvg:")], view_box


def build_single_path_svg(path_d: str, view_box: list[float]) -> str:
    view_box_text = " ".join(f"{value:g}" for value in view_box)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{view_box_text}"><path d="{path_d}"/></svg>'
    )


def parse_drawing(drawing: str) -> list[tuple[str, list[tuple[float, float]]]]:
    tokens = drawing.split()
    commands: list[tuple[str, list[tuple[float, float]]]] = []
    index = 0
    while index < len(tokens):
        cmd = tokens[index]
        index += 1
        if cmd in {"m", "n", "l"}:
            x = float(tokens[index])
            y = float(tokens[index + 1])
            commands.append((cmd, [(x, y)]))
            index += 2
            continue
        if cmd == "b":
            points = []
            for _ in range(3):
                x = float(tokens[index])
                y = float(tokens[index + 1])
                points.append((x, y))
                index += 2
            commands.append((cmd, points))
            continue
        raise RuntimeError(f"Unsupported ASS drawing command: {cmd}")
    return commands


def format_coord(value: float) -> str:
    rounded = round(value, 2)
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    return text if text else "0"


def serialize_drawing(commands: list[tuple[str, list[tuple[float, float]]]]) -> str:
    parts: list[str] = []
    for cmd, points in commands:
        parts.append(cmd)
        for x, y in points:
            parts.append(format_coord(x))
            parts.append(format_coord(y))
    return " ".join(parts)


def transform_drawing(
    drawing: str,
    *,
    scale: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> str:
    commands = parse_drawing(drawing)
    transformed: list[tuple[str, list[tuple[float, float]]]] = []
    for cmd, points in commands:
        transformed_points = [((x + offset_x) * scale, (y + offset_y) * scale) for x, y in points]
        transformed.append((cmd, transformed_points))
    return serialize_drawing(transformed)


def drawing_bbox(drawing: str) -> list[float]:
    commands = parse_drawing(drawing)
    xs: list[float] = []
    ys: list[float] = []
    for _, points in commands:
        for x, y in points:
            xs.append(x)
            ys.append(y)
    return [min(xs), min(ys), max(xs), max(ys)]


def run_svg2ass(paths: WordFxPaths, config: WordFxConfig, temp_svg_path: Path) -> str:
    command = [
        str(paths.svg2ass_exe),
        "-a",
        "0",
        "-f",
        "2",
        "-s",
        str(config.draw_p_scale),
        str(temp_svg_path).replace("\\", "/"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    match = DRAWING_RE.search(result.stdout)
    if not match:
        raise RuntimeError(f"Unable to extract drawing from svg2ass output: {result.stdout[:200]}")
    if int(match.group("scale")) != config.draw_p_scale:
        raise RuntimeError(f"Unexpected drawing scale from svg2ass: {match.group('scale')}")
    return match.group("drawing").strip()


def build_animcjk_asset(
    char: str,
    source_name: str,
    svg_text: str,
    *,
    paths: WordFxPaths,
    config: WordFxConfig,
) -> GlyphAsset:
    strokes, view_box = parse_animcjk_strokes(svg_text, char)
    if not strokes:
        return GlyphAsset(
            char=char,
            unicode=unicode_tag(char),
            source=source_name,
            mode="whole_char",
            font_family=config.font_name,
            stroke_count=0,
            view_box=view_box,
            strokes=[],
            debug={"reason": "no_strokes_found"},
        )

    view_x, view_y, view_w, view_h = view_box
    normalizer = config.font_size / max(view_w, view_h)
    offset_x = -(view_x * config.draw_p_scale)
    offset_y = -(view_y * config.draw_p_scale)
    converted_strokes: list[StrokeAsset] = []
    source_code = codepoint_decimal(char)
    for index, (path_d, median_points) in enumerate(strokes, start=1):
        temp_svg_path = paths.temp_svg_dir / f"{source_name}_{source_code}_{index:02d}.svg"
        temp_svg_path.write_text(build_single_path_svg(path_d, view_box), encoding="utf-8")
        raw_drawing = run_svg2ass(paths, config, temp_svg_path)
        normalized_drawing = transform_drawing(
            raw_drawing,
            scale=normalizer,
            offset_x=offset_x,
            offset_y=offset_y,
        )
        normalized_bbox = [round(value, 2) for value in drawing_bbox(normalized_drawing)]
        converted_strokes.append(
            StrokeAsset(
                index=index - 1,
                ass_path=normalized_drawing,
                bbox=normalized_bbox,
                entry_vector=[0.0, -1.0],
                entry_distance=1.25,
                source_median=median_points[:4],
            )
        )

    return GlyphAsset(
        char=char,
        unicode=unicode_tag(char),
        source=source_name,
        mode="full_stroke",
        font_family=config.font_name,
        stroke_count=len(converted_strokes),
        view_box=view_box,
        strokes=converted_strokes,
        debug={
            "normalizer": round(normalizer, 6),
            "asset_cache_version": config.asset_cache_version,
        },
    )


def build_kanjivg_asset(char: str, svg_text: str, config: WordFxConfig) -> GlyphAsset:
    paths, view_box = parse_kanjivg_paths(svg_text)
    return GlyphAsset(
        char=char,
        unicode=unicode_tag(char),
        source="kanjivg",
        mode="whole_char",
        font_family=config.font_name,
        stroke_count=0,
        view_box=view_box,
        strokes=[],
        debug={"reason": "kanjivg_fallback", "path_count": len(paths)},
    )


def download_glyph_asset(char: str, *, paths: WordFxPaths, config: WordFxConfig) -> GlyphAsset:
    if is_han(char):
        anim_path = paths.download_dir / "animcjk_ja" / f"{codepoint_decimal(char)}.svg"
        anim_svg = download_text(
            config.animcjk_ja_url.format(code=codepoint_decimal(char)),
            anim_path,
            config.request_timeout,
        )
        if anim_svg:
            return build_animcjk_asset(char, "animcjk_ja", anim_svg, paths=paths, config=config)

        kanjivg_path = paths.download_dir / "kanjivg" / f"{codepoint_hex5(char)}.svg"
        kanjivg_svg = download_text(
            config.kanjivg_url.format(code=codepoint_hex5(char)),
            kanjivg_path,
            config.request_timeout,
        )
        if kanjivg_svg:
            return build_kanjivg_asset(char, kanjivg_svg, config)

        return GlyphAsset(
            char=char,
            unicode=unicode_tag(char),
            source="missing",
            mode="whole_char",
            font_family=config.font_name,
            stroke_count=0,
            view_box=[0.0, 0.0, 1024.0, 1024.0],
            strokes=[],
            debug={"reason": "han_missing"},
        )

    if is_kana(char):
        kana_path = paths.download_dir / "animcjk_ja_kana" / f"{codepoint_decimal(char)}.svg"
        kana_svg = download_text(
            config.animcjk_ja_kana_url.format(code=codepoint_decimal(char)),
            kana_path,
            config.request_timeout,
        )
        if kana_svg:
            return build_animcjk_asset(char, "animcjk_ja_kana", kana_svg, paths=paths, config=config)
        return GlyphAsset(
            char=char,
            unicode=unicode_tag(char),
            source="missing",
            mode="whole_char",
            font_family=config.font_name,
            stroke_count=0,
            view_box=[0.0, 0.0, 1024.0, 1024.0],
            strokes=[],
            debug={"reason": "kana_missing"},
        )

    return GlyphAsset(
        char=char,
        unicode=unicode_tag(char),
        source="default",
        mode="whole_char",
        font_family=config.font_name,
        stroke_count=0,
        view_box=[0.0, 0.0, 1024.0, 1024.0],
        strokes=[],
        debug={"reason": "non_ja_char"},
    )


def asset_path_for_char(paths: WordFxPaths, char: str) -> Path:
    return paths.glyph_asset_dir / f"{unicode_tag(char)}.json"


def glyph_asset_from_dict(data: dict) -> GlyphAsset:
    strokes = [StrokeAsset(**stroke) for stroke in data["strokes"]]
    return GlyphAsset(
        char=data["char"],
        unicode=data["unicode"],
        source=data["source"],
        mode=data["mode"],
        font_family=data["font_family"],
        stroke_count=data["stroke_count"],
        view_box=data["view_box"],
        strokes=strokes,
        debug=data["debug"],
    )


def load_or_build_asset(char: str, *, paths: WordFxPaths, config: WordFxConfig) -> GlyphAsset:
    asset_path = asset_path_for_char(paths, char)
    if asset_path.exists():
        data = json.loads(asset_path.read_text(encoding="utf-8"))
        legacy_kana_whole_char = (
            is_kana(char)
            and data.get("source") == "animcjk_ja_kana"
            and data.get("mode") == "whole_char"
        )
        cached_version = data.get("debug", {}).get("asset_cache_version", 0)
        if not legacy_kana_whole_char and cached_version >= config.asset_cache_version:
            return glyph_asset_from_dict(data)

    asset = download_glyph_asset(char, paths=paths, config=config)
    asset_path.write_text(json.dumps(asdict(asset), ensure_ascii=False, indent=2), encoding="utf-8")
    return asset


def build_word_assets(
    chars: list[str] | set[str],
    *,
    paths: WordFxPaths,
    config: WordFxConfig,
) -> dict[str, GlyphAsset]:
    ensure_dirs(paths)
    return {char: load_or_build_asset(char, paths=paths, config=config) for char in sorted(chars)}


def build_word_assets_for_lines(
    lines: list[str],
    *,
    paths: WordFxPaths,
    config: WordFxConfig,
) -> dict[str, GlyphAsset]:
    unique_chars = {
        char
        for line in lines
        for char in line
        if is_drawable_char(char)
    }
    return build_word_assets(unique_chars, paths=paths, config=config)
