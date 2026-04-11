"""Spike melt effect renderer for ASS subtitle lines."""

from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import os
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from typing import Any
from shapely import affinity
from shapely.errors import GEOSException, ShapelyError
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

from pyonfx import Ass, Line, Shape, Utils
from pyonfx.convert import Convert


LAYERS_PER_LINE = 3
SPIKE_BASE = Shape.ellipse(3, 20)
SPIKE_BASE_CENTERED = Shape(SPIKE_BASE.drawing_cmds)
SPIKE_BASE_CENTERED.align(an=5)
SPIKE_BASE_DRAWING = str(SPIKE_BASE_CENTERED)
_SPIKE_TAG_CACHE: dict[tuple[float, float, float], str] = {}
_NOISE_MASK_LIBRARY_CACHE: dict[tuple[int, int, int, int, float, int], list[list[MultiPolygon]]] = {}


@dataclass(frozen=True, slots=True)
class MeltConfig:
    mask_steps: int = 32
    adaptive_mask_steps: bool = False
    adaptive_mask_area_ref: float = 3600.0
    mask_irregularity: float = 0.34
    mask_detail_points: int = 28
    mask_library_size: int = 20
    mask_noise_resolution: int = 26
    mask_noise_octaves: int = 4
    mask_noise_scale: float = 3.4
    mask_noise_simplify: float = 0.0
    line_lead_in_ms: int = 320
    line_fade_in_ms: int = 90
    line_highlight_ms: int = 70
    line_pop_ms: int = 140
    line_pop_scale_percent: int = 120
    line_highlight_strength: float = 0.42
    syllable_stagger_ms: int = 80
    dissolve_duration: int = 1000
    pixel_fade_ms: int = 180
    death_quantize_ms: int = 10
    drawing_min_point_spacing: float = 0.75
    mask_min_piece_area: float = 0.2
    output_coord_precision: int = 2
    line_base_blur: float = 0.0
    line_dual_glow_enabled: bool = True
    line_glow_outer_blur: float = 16.0
    mask_piece_blur: float = 0.0
    merge_mask_bands_by_timing: bool = True
    spike_total_count: int = 32
    spike_min_count: int = 12
    spike_count_per_100ms: float = 3.2
    spike_lifetime_ms: int = 300
    spike_travel_distance: float = 44.0
    spike_angle_range: float = 20.0
    spike_spawn_jitter: float = 0.3
    spike_bound_margin: float = 18.0
    spike_early_start_ms: int = 300
    spike_lead_ms: int = 70
    spike_lifetime_min_ms: int = 40
    spike_lifetime_max_ms: int = 600
    spike_speed_min: float = 36.0
    spike_speed_max: float = 175.0
    spike_radial_speed: float = 14.0
    spike_cone_half_angle: float = 25.0
    spike_spherical_jitter: float = 0.1
    spike_emit_peak_t: float = 0.29094324
    spike_size_peak_t: float = 0.04815741
    spike_alpha_peak_t: float = 20239 / 65535
    spike_spawn_radius_factor: float = 0.38
    spike_stretch_percent: int = 1600
    spike_scale_x_min: float = 0.6
    spike_scale_x_max: float = 1.0
    spike_scale_y_min: float = 0.06
    spike_scale_y_max: float = 0.12
    spike_glow_enabled: bool = True
    spike_glow_inner_scale: float = 1.18
    spike_glow_outer_scale: float = 1.32
    spike_glow_inner_blur: float = 2.0
    spike_glow_outer_blur: float = 4.0
    spike_glow_inner_alpha: str = "&H82&"
    spike_glow_outer_alpha: str = "&HB8&"
    mask_preroll_ms: int = 80
    glyph_shake_duration_ms: int = 160
    glyph_shake_shift_px: float = 3.0
    glyph_shake_rot_deg: float = 2.4
    predissolve_spike_window_ms: int = 300
    predissolve_spike_start_advance_ms: int = 180
    predissolve_spike_count: int = 8
    predissolve_spike_min_count: int = 4
    predissolve_spike_count_per_100ms: float = 2.7
    predissolve_spike_lifetime_ms: int = 360
    predissolve_spike_accel: float = 2.2
    predissolve_spike_travel_multiplier: float = 1.9
    predissolve_spike_angle_multiplier: float = 1.35
    predissolve_spike_scale_x: float = 0.95
    predissolve_spike_scale_y: float = 1.45
    predissolve_spike_bound_extra: float = 14.0
    spike_angle_cache_step: float = 2.0
    spike_scale_cache_precision: int = 2
    enable_multiprocessing: bool = True
    multiprocessing_min_lines: int = 6
    max_workers: int = 0
    random_seed: int = 24681357
    quality_preset: str = "quality"
    compression_preset: str = "none"


@dataclass(frozen=True, slots=True)
class OutputEvent:
    layer: int
    style: str
    start_time: int
    end_time: int
    text: str


@dataclass(frozen=True, slots=True)
class LayerShape:
    multipolygon: MultiPolygon
    color: str
    alpha: str
    layer_offset: int
    bounds: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class DissolveBandTiming:
    end_time: int
    t_fade_on: int
    t_fade_off: int


# Geometry helpers
def _ensure_multipolygon(geom) -> MultiPolygon:
    if geom is None or geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, GeometryCollection):
        polygons = [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
        return MultiPolygon(polygons) if polygons else MultiPolygon()
    return MultiPolygon()


def _coerce_valid_multipolygon(geom) -> MultiPolygon | None:
    if geom is None or geom.is_empty:
        return MultiPolygon()

    if isinstance(geom, MultiPolygon):
        return geom if geom.is_valid else None

    if isinstance(geom, Polygon):
        return MultiPolygon([geom]) if geom.is_valid else None

    if isinstance(geom, GeometryCollection):
        polygons = [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
        if not polygons:
            return MultiPolygon()
        return MultiPolygon(polygons) if all(poly.is_valid for poly in polygons) else None

    return None


def _repair_geometry(geom) -> MultiPolygon:
    fast_path = _coerce_valid_multipolygon(geom)
    if fast_path is not None:
        return fast_path

    repaired = geom
    try:
        repaired = make_valid(repaired)
    except (GEOSException, ShapelyError, ValueError, TypeError):
        pass

    repaired = _ensure_multipolygon(repaired)
    if repaired.is_empty:
        return repaired

    if not repaired.is_valid:
        try:
            repaired = _ensure_multipolygon(repaired.buffer(0))
        except (GEOSException, ShapelyError, ValueError, TypeError):
            pass

    if not repaired.is_valid:
        try:
            repaired = _ensure_multipolygon(make_valid(repaired.buffer(0)))
        except (GEOSException, ShapelyError, ValueError, TypeError):
            pass

    return repaired


def _bounds_intersect(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return not (
        left[2] <= right[0]
        or right[2] <= left[0]
        or left[3] <= right[1]
        or right[3] <= left[1]
    )


def _safe_difference(outer_geom, inner_geom, *, shrink_fallback: float | None = None) -> MultiPolygon:
    outer_fixed = _repair_geometry(outer_geom)
    inner_fixed = _repair_geometry(inner_geom)
    if outer_fixed.is_empty:
        return MultiPolygon()
    if inner_fixed.is_empty:
        return outer_fixed

    try:
        return _repair_geometry(outer_fixed.difference(inner_fixed))
    except (GEOSException, ShapelyError, ValueError, TypeError):
        pass

    try:
        return _repair_geometry(outer_fixed.buffer(0).difference(inner_fixed.buffer(0)))
    except (GEOSException, ShapelyError, ValueError, TypeError):
        pass

    if shrink_fallback is not None:
        try:
            shrunken_inner = _repair_geometry(inner_fixed.buffer(shrink_fallback))
            if not shrunken_inner.is_empty:
                return _repair_geometry(outer_fixed.difference(shrunken_inner))
        except (GEOSException, ShapelyError, ValueError, TypeError):
            pass

    return outer_fixed


def _safe_intersection(geom, mask_geom) -> MultiPolygon:
    try:
        piece = geom.intersection(mask_geom)
    except (GEOSException, ShapelyError, ValueError, TypeError):
        try:
            piece = geom.buffer(0).intersection(mask_geom.buffer(0))
        except (GEOSException, ShapelyError, ValueError, TypeError):
            return MultiPolygon()

    fast_path = _coerce_valid_multipolygon(piece)
    if fast_path is not None:
        return fast_path
    return _repair_geometry(piece)


def _fade_target_alpha(alpha: str) -> str:
    return alpha if alpha != "&H00&" else "&H00&"


def _syl_origin(syl) -> tuple[int, int]:
    return math.floor(syl.left), math.floor(syl.top)


# Shape construction
def text_to_layer_shapes(obj) -> list[LayerShape]:
    style = obj.styleref
    shape = Convert.text_to_shape(obj).move(obj.left % 1, obj.top % 1)
    fill_mp = _repair_geometry(shape.to_multipolygon())
    if fill_mp.is_empty:
        return []

    layers = [
        LayerShape(
            multipolygon=fill_mp,
            color=style.color1,
            alpha=style.alpha1,
            layer_offset=1,
            bounds=fill_mp.bounds,
        )
    ]

    if style.outline <= 0:
        return layers

    bord_mp = _repair_geometry(fill_mp.buffer(style.outline, join_style=1, cap_style=1))
    if bord_mp.is_empty:
        return layers

    bord_ring = _safe_difference(bord_mp, fill_mp)
    if not bord_ring.is_empty:
        layers.insert(
            0,
            LayerShape(
                multipolygon=bord_ring,
                color=style.color3,
                alpha=style.alpha3,
                layer_offset=0,
                bounds=bord_ring.bounds,
            ),
        )

    return layers


def _build_mask_band_polygons(
    bounds: tuple[float, float, float, float],
    steps: int,
    pattern_idx: int,
    config: MeltConfig,
    rng: random.Random,
) -> list[Polygon]:
    library = _get_noise_mask_library(config, steps)
    if library:
        template = library[rng.randrange(len(library))]
        scaled = _scale_noise_template_to_bounds(template, bounds)
        if scaled:
            return scaled

    return _build_fallback_mask_band_polygons(bounds, steps, pattern_idx, config, rng)


def _build_fallback_mask_band_polygons(
    bounds: tuple[float, float, float, float],
    steps: int,
    pattern_idx: int,
    config: MeltConfig,
    rng: random.Random,
) -> list[Polygon]:
    min_x, min_y, max_x, max_y = bounds
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    pad = max(width, height) * 2.0 + 8.0
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5

    def _sample_wobble(
        count: int,
        amplitude: float,
        phase: float,
        bias: float = 0.0,
    ) -> list[float]:
        if count <= 1 or amplitude <= 0.0:
            return [bias] * max(1, count)

        waves = rng.uniform(1.4, 2.8)
        high_waves = waves * rng.uniform(3.2, 4.4)
        return [
            bias
            + amplitude
            * (
                0.50 * math.sin((math.pi * 2.0 * waves * i / (count - 1)) + phase)
                + 0.24 * math.sin((math.pi * 2.0 * (waves * 1.9) * i / (count - 1)) - phase * 0.7)
                + 0.14 * math.sin((math.pi * 2.0 * (waves * 3.2) * i / (count - 1)) + phase * 1.3)
                + 0.12 * math.sin((math.pi * 2.0 * high_waves * i / (count - 1)) - phase * 1.1)
                + 0.10 * rng.uniform(-1.0, 1.0)
            )
            for i in range(count)
        ]

    def _build_directional_band(axis: tuple[float, float], t0: float, t1: float) -> Polygon:
        axis_x, axis_y = axis
        axis_len = math.hypot(axis_x, axis_y)
        if axis_len <= 0.0:
            return Polygon()

        ux = axis_x / axis_len
        uy = axis_y / axis_len
        vx = -uy
        vy = ux
        progress_span = abs(width * ux) + abs(height * uy) + pad * 2.0
        cross_span = abs(width * vx) + abs(height * vy) + pad * 2.0
        samples = max(6, config.mask_detail_points)
        phase = rng.uniform(0.0, math.pi * 2.0)
        base_half = progress_span * (t1 - t0) * 0.5
        min_half = max(progress_span / max(steps * 6.0, 1.0), 0.75)
        center_dist = (-progress_span * 0.5) + ((t0 + t1) * 0.5 * progress_span)
        center_amp = min(progress_span * config.mask_irregularity * 0.12, base_half * 0.65)
        width_amp = min(progress_span * config.mask_irregularity * 0.08, base_half * 0.45)
        center_offsets = _sample_wobble(samples, center_amp, phase)
        half_offsets = _sample_wobble(samples, width_amp, phase + 1.1, bias=base_half)

        outer: list[tuple[float, float]] = []
        inner: list[tuple[float, float]] = []
        for idx in range(samples):
            if samples == 1:
                sn = 0.0
            else:
                sn = (idx / (samples - 1)) - 0.5

            lateral = sn * cross_span
            half = max(min_half, half_offsets[idx])
            dist0 = center_dist + center_offsets[idx] - half
            dist1 = center_dist + center_offsets[idx] + half
            base_x = cx + vx * lateral
            base_y = cy + vy * lateral
            outer.append((base_x + ux * dist1, base_y + uy * dist1))
            inner.append((base_x + ux * dist0, base_y + uy * dist0))

        return Polygon([*outer, *reversed(inner)])

    def _build_irregular_box(half_w: float, half_h: float, phase: float) -> Polygon:
        samples = max(4, config.mask_detail_points // 2)
        amp_x = min(width * config.mask_irregularity * 0.24, max(0.0, half_w) * 0.52 + 1.2)
        amp_y = min(height * config.mask_irregularity * 0.24, max(0.0, half_h) * 0.52 + 1.2)
        top_offsets = _sample_wobble(samples, amp_y, phase)
        right_offsets = _sample_wobble(samples, amp_x, phase + 0.9)
        bottom_offsets = _sample_wobble(samples, amp_y, phase + 1.8)
        left_offsets = _sample_wobble(samples, amp_x, phase + 2.7)

        points: list[tuple[float, float]] = []
        for idx in range(samples):
            s = idx / (samples - 1)
            x = cx - half_w + (2.0 * half_w * s)
            points.append((x, cy - half_h + top_offsets[idx]))
        for idx in range(1, samples):
            s = idx / (samples - 1)
            y = cy - half_h + (2.0 * half_h * s)
            points.append((cx + half_w + right_offsets[idx], y))
        for idx in range(samples - 2, -1, -1):
            s = idx / (samples - 1)
            x = cx - half_w + (2.0 * half_w * s)
            points.append((x, cy + half_h + bottom_offsets[idx]))
        for idx in range(samples - 2, 0, -1):
            s = idx / (samples - 1)
            y = cy - half_h + (2.0 * half_h * s)
            points.append((cx - half_w + left_offsets[idx], y))

        return Polygon(points)

    direction_patterns: dict[int, tuple[float, float]] = {
        0: (1.0, 0.0),
        1: (-1.0, 0.0),
        2: (0.0, 1.0),
        3: (0.0, -1.0),
        4: (1.0, 1.0),
        5: (-1.0, 1.0),
    }
    bands: list[Polygon] = []
    for step_idx in range(steps):
        t0 = step_idx / steps
        t1 = (step_idx + 1) / steps

        axis = direction_patterns.get(pattern_idx)
        if axis is not None:
            band = _build_directional_band(axis, t0, t1)
        else:
            half_w0 = (width * t0) * 0.5
            half_w1 = (width * t1) * 0.5
            half_h0 = (height * t0) * 0.5
            half_h1 = (height * t1) * 0.5
            phase = rng.uniform(0.0, math.pi * 2.0)
            outer = _repair_geometry(_build_irregular_box(half_w1, half_h1, phase))
            if step_idx == 0:
                band = outer
            else:
                inner = _repair_geometry(_build_irregular_box(half_w0, half_h0, phase + 0.65))
                band = _safe_difference(outer, inner, shrink_fallback=-0.35)

        bands.append(_repair_geometry(band))

    return bands


def _fade_curve(value: float) -> float:
    return value * value * value * (value * (value * 6.0 - 15.0) + 10.0)


def _lerp(left: float, right: float, t: float) -> float:
    return left + (right - left) * t


def _smoothstep01(value: float) -> float:
    clamped = max(0.0, min(1.0, value))
    return clamped * clamped * (3.0 - 2.0 * clamped)


def _gradient_vector(ix: int, iy: int, seed: int) -> tuple[float, float]:
    hashed = (ix * 1836311903) ^ (iy * 2971215073) ^ (seed * 4807526976)
    angle = (hashed & 0xFFFFFFFF) / 0xFFFFFFFF * math.tau
    return math.cos(angle), math.sin(angle)


def _perlin_noise_2d(x: float, y: float, seed: int) -> float:
    x0 = math.floor(x)
    y0 = math.floor(y)
    x1 = x0 + 1
    y1 = y0 + 1
    sx = x - x0
    sy = y - y0

    g00 = _gradient_vector(x0, y0, seed)
    g10 = _gradient_vector(x1, y0, seed)
    g01 = _gradient_vector(x0, y1, seed)
    g11 = _gradient_vector(x1, y1, seed)

    n00 = g00[0] * (x - x0) + g00[1] * (y - y0)
    n10 = g10[0] * (x - x1) + g10[1] * (y - y0)
    n01 = g01[0] * (x - x0) + g01[1] * (y - y1)
    n11 = g11[0] * (x - x1) + g11[1] * (y - y1)

    u = _fade_curve(sx)
    v = _fade_curve(sy)
    nx0 = _lerp(n00, n10, u)
    nx1 = _lerp(n01, n11, u)
    return _lerp(nx0, nx1, v)


def _fbm_noise_2d(x: float, y: float, seed: int, octaves: int) -> float:
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    norm = 0.0

    for octave in range(max(1, octaves)):
        total += _perlin_noise_2d(x * frequency, y * frequency, seed + octave * 1013) * amplitude
        norm += amplitude
        amplitude *= 0.5
        frequency *= 2.0

    if norm <= 0.0:
        return 0.0
    return total / norm


def _generate_noise_mask_template(
    *,
    steps: int,
    resolution: int,
    octaves: int,
    scale: float,
    simplify_tolerance: float,
    seed: int,
) -> list[MultiPolygon]:
    grid = max(6, resolution)
    cell_size = 1.0 / grid
    cells: list[tuple[float, Polygon]] = []

    for row in range(grid):
        for col in range(grid):
            x0 = col * cell_size
            y0 = row * cell_size
            x1 = x0 + cell_size
            y1 = y0 + cell_size
            sample_x = ((col + 0.5) / grid) * scale
            sample_y = ((row + 0.5) / grid) * scale
            value = _fbm_noise_2d(sample_x, sample_y, seed, octaves)
            cells.append((value, Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])))

    if not cells:
        return []

    cells.sort(key=lambda item: item[0])
    total_cells = len(cells)
    bands: list[MultiPolygon] = []

    for step_idx in range(max(1, steps)):
        start_idx = int(round((step_idx / steps) * total_cells))
        end_idx = int(round(((step_idx + 1) / steps) * total_cells))
        step_cells = [cell for _, cell in cells[start_idx:end_idx]]
        if not step_cells:
            bands.append(MultiPolygon())
            continue

        merged = _repair_geometry(unary_union(step_cells))
        if simplify_tolerance > 0.0 and not merged.is_empty:
            merged = _repair_geometry(merged.simplify(simplify_tolerance, preserve_topology=True))
        bands.append(merged)

    return bands


def _get_noise_mask_library(config: MeltConfig, steps: int) -> list[list[MultiPolygon]]:
    cache_key = (
        max(1, steps),
        max(1, config.mask_library_size),
        max(6, config.mask_noise_resolution),
        max(1, config.mask_noise_octaves),
        float(config.mask_noise_scale),
        int(round(config.mask_noise_simplify * 1000.0)),
        int(config.random_seed),
    )
    cached = _NOISE_MASK_LIBRARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    library: list[list[MultiPolygon]] = []
    base_seed = int(config.random_seed)
    for idx in range(max(1, config.mask_library_size)):
        seed = base_seed + idx * 7919
        template = _generate_noise_mask_template(
            steps=steps,
            resolution=config.mask_noise_resolution,
            octaves=config.mask_noise_octaves,
            scale=max(0.1, config.mask_noise_scale),
            simplify_tolerance=max(0.0, config.mask_noise_simplify),
            seed=seed,
        )
        if template:
            library.append(template)

    _NOISE_MASK_LIBRARY_CACHE[cache_key] = library
    return library


def _scale_noise_template_to_bounds(
    template: Sequence[MultiPolygon],
    bounds: tuple[float, float, float, float],
) -> list[Polygon]:
    min_x, min_y, max_x, max_y = bounds
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    scaled: list[Polygon] = []

    for band in template:
        if band.is_empty:
            scaled.append(MultiPolygon())
            continue

        scaled_band = affinity.scale(band, xfact=width, yfact=height, origin=(0.0, 0.0))
        scaled_band = affinity.translate(scaled_band, xoff=min_x, yoff=min_y)
        scaled.append(_repair_geometry(scaled_band))

    return scaled


def _shape_to_ass_drawing(multipolygon: MultiPolygon, min_point_spacing: float) -> str:
    if multipolygon.is_empty:
        return ""
    return str(
        Shape.from_multipolygon(
            multipolygon,
            min_point_spacing=max(0.1, min_point_spacing),
        )
    )


# Timing helpers
def _get_adjusted_syllable_times(line, syl, config: MeltConfig) -> tuple[int, int]:
    orig_start = int(line.start_time + syl.start_time)
    orig_end = int(line.start_time + syl.end_time)
    duration = max(1, orig_end - orig_start)
    adjusted_start = int(line.start_time + max(0, syl.i) * max(0, config.syllable_stagger_ms))
    adjusted_end = adjusted_start + duration
    return adjusted_start, adjusted_end


def _get_karaoke_syllable_times(line, syl) -> tuple[int, int]:
    return int(line.start_time + syl.start_time), int(line.start_time + syl.end_time)


def _get_glyph_motion_profile(line, syl, config: MeltConfig) -> tuple[int, int, float, float, float]:
    dissolve_start = int(line.start_time + syl.end_time)
    base_shift = max(0.0, config.glyph_shake_shift_px)
    base_rot = max(0.0, config.glyph_shake_rot_deg)
    duration = max(40, int(config.glyph_shake_duration_ms * 1.3))
    start_abs = dissolve_start - duration
    end_abs = dissolve_start
    sign = -1.0 if ((line.i + syl.i) % 2 == 0) else 1.0
    dx = sign * base_shift * 1.08
    dy = -base_shift * 0.3
    frz = sign * base_rot * 1.05
    return start_abs, end_abs, dx, dy, frz


# Event builders
def _build_full_shape_events(
    line,
    syl,
    layers: Sequence[LayerShape],
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
) -> list[OutputEvent]:
    if not layers:
        return []

    lead_in_ms = max(0, config.line_lead_in_ms)
    fade_in_ms = max(1, config.line_fade_in_ms)
    highlight_ms = max(0, config.line_highlight_ms)
    pop_ms = max(1, config.line_pop_ms)
    pop_scale = max(100, int(config.line_pop_scale_percent))
    syl_start, _ = _get_adjusted_syllable_times(line, syl, config)
    _, dissolve_anchor = _get_karaoke_syllable_times(line, syl)
    motion_start_abs, motion_end_abs, motion_dx, motion_dy, motion_frz = _get_glyph_motion_profile(
        line, syl, config
    )
    full_start = max(0, syl_start - lead_in_ms)
    full_end = max(full_start + 1, dissolve_anchor)
    syl_left, syl_top = _syl_origin(syl)
    precision = max(0, int(config.output_coord_precision))
    start_x = _format_ass_number_with_precision(syl_left, precision)
    start_y = _format_ass_number_with_precision(syl_top, precision)
    end_x = _format_ass_number_with_precision(syl_left + motion_dx, precision)
    end_y = _format_ass_number_with_precision(syl_top + motion_dy, precision)
    frz_text = _format_ass_number_with_precision(motion_frz, precision)
    events: list[OutputEvent] = []

    for layer in layers:
        drawing = _shape_to_ass_drawing(
            layer.multipolygon,
            config.drawing_min_point_spacing,
        )
        if not drawing:
            continue

        t_highlight = highlight_ms
        t_pop_end = highlight_ms + pop_ms
        t_settle_end = t_pop_end + fade_in_ms
        target_alpha = _fade_target_alpha(layer.alpha)
        highlight_color = _boost_ass_color(layer.color, config.line_highlight_strength)
        move_start = max(0, motion_start_abs - full_start)
        move_end = max(move_start + 1, min(full_end, motion_end_abs) - full_start)
        base_blur = _format_ass_number(max(0.0, float(config.line_base_blur)))
        glow_outer_blur = _format_ass_number(max(0.0, float(config.line_glow_outer_blur)))

        if config.line_dual_glow_enabled:
            outer_text = (
                f"{{\\p1\\an7\\move({start_x},{start_y},{end_x},{end_y},{move_start},{move_end})\\1c{highlight_color}\\1a&HFF&"
                f"\\blur{glow_outer_blur}\\fscx96\\fscy96"
                f"\\t(0,{t_highlight},0.35,\\1a{target_alpha}\\blur{glow_outer_blur})"
                f"\\t(0,{t_pop_end},0.5,\\fscx{pop_scale}\\fscy{pop_scale}\\1c{highlight_color})"
                f"\\t({t_highlight},{t_settle_end},1.3,\\fscx100\\fscy100\\1c{layer.color}\\blur{glow_outer_blur})"
                f"\\t({move_start},{move_end},\\frz{frz_text})"
                f"}}{drawing}"
            )
            events.append(
                OutputEvent(
                    layer=line_layer_base + layer.layer_offset - 2,
                    style=style_name,
                    start_time=int(full_start),
                    end_time=int(full_end),
                    text=outer_text,
                )
            )

        text = (
            f"{{\\p1\\an7\\move({start_x},{start_y},{end_x},{end_y},{move_start},{move_end})\\1c{highlight_color}\\1a&HFF&"
            f"\\blur{base_blur}\\fscx96\\fscy96"
            f"\\t(0,{t_highlight},0.35,\\1a{target_alpha}\\blur{base_blur})"
            f"\\t(0,{t_pop_end},0.5,\\fscx{pop_scale}\\fscy{pop_scale}\\1c{highlight_color})"
            f"\\t({t_highlight},{t_settle_end},1.3,\\fscx100\\fscy100\\1c{layer.color}\\blur{base_blur})"
            f"\\t({move_start},{move_end},\\frz{frz_text})"
            f"}}{drawing}"
        )

        events.append(
            OutputEvent(
                layer=line_layer_base + layer.layer_offset,
                style=style_name,
                start_time=int(full_start),
                end_time=int(full_end),
                text=text,
            )
        )

    return events


def _collect_shape_points(layers: Sequence[LayerShape]) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    for layer in layers:
        for polygon in layer.multipolygon.geoms:
            for ring in (polygon.exterior, *polygon.interiors):
                for x, y in ring.coords:
                    key = (int(round(x)), int(round(y)))
                    if key in seen:
                        continue
                    seen.add(key)
                    points.append(key)

    return points


def _build_vector_mask_events(
    line,
    syl,
    layers: Sequence[LayerShape],
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
    rng: random.Random,
) -> list[OutputEvent]:
    if not layers:
        return []

    bounds = layers[0].multipolygon.bounds
    steps = _resolve_effective_mask_steps(bounds, config)
    syl_start, dissolve_anchor = _get_karaoke_syllable_times(line, syl)
    motion_start_abs, motion_end_abs, motion_dx, motion_dy, motion_frz = _get_glyph_motion_profile(
        line, syl, config
    )
    dissolve_start = dissolve_anchor
    event_start = max(syl_start, dissolve_start - max(0, config.mask_preroll_ms))
    effective_window = max(1, config.dissolve_duration - config.pixel_fade_ms)
    death_quantize_ms = _resolve_death_quantize_ms(config)
    mask_preroll_ms = max(0, config.mask_preroll_ms)
    pattern_idx = rng.randrange(7)
    band_masks = _build_mask_band_polygons(bounds, steps, pattern_idx, config, rng)
    events: list[OutputEvent] = []
    syl_left, syl_top = _syl_origin(syl)
    move_tag = _build_motion_tag(
        event_start=event_start,
        syl_left=syl_left,
        syl_top=syl_top,
        motion_start_abs=motion_start_abs,
        motion_end_abs=motion_end_abs,
        motion_dx=motion_dx,
        motion_dy=motion_dy,
        motion_frz=motion_frz,
        output_coord_precision=config.output_coord_precision,
    )
    layer_alpha_pairs = [(layer, _fade_target_alpha(layer.alpha)) for layer in layers]
    dissolve_blur = max(
        max(0.0, float(config.mask_piece_blur)),
        max(0.0, float(config.line_base_blur)),
    )
    band_entries = _collapse_band_masks_by_timing(
        band_masks=band_masks,
        steps=steps,
        dissolve_start=dissolve_start,
        event_start=event_start,
        effective_window=effective_window,
        pixel_fade_ms=config.pixel_fade_ms,
        death_quantize_ms=death_quantize_ms,
        merge_by_timing=config.merge_mask_bands_by_timing,
    )

    for band_mask, timing in band_entries:
        if band_mask.is_empty:
            continue

        band_bounds = band_mask.bounds
        for layer, target_alpha in layer_alpha_pairs:
            piece = _clip_layer_to_band(layer, band_mask, band_bounds)
            if piece.is_empty:
                continue

            event = _build_mask_piece_event(
                piece=piece,
                layer=layer,
                line_layer_base=line_layer_base,
                style_name=style_name,
                event_start=event_start,
                move_tag=move_tag,
                target_alpha=target_alpha,
                timing=timing,
                drawing_min_point_spacing=config.drawing_min_point_spacing,
                mask_preroll_ms=mask_preroll_ms,
                min_piece_area=config.mask_min_piece_area,
                mask_piece_blur=dissolve_blur,
            )
            if event is not None:
                events.append(event)

    return events


def _build_dissolve_band_timing(
    *,
    step_idx: int,
    steps: int,
    dissolve_start: int,
    event_start: int,
    effective_window: int,
    pixel_fade_ms: int,
    death_quantize_ms: int,
) -> DissolveBandTiming:
    death_ms = int(dissolve_start + (effective_window * ((step_idx + 1) / steps)))
    death_ms = int(round(death_ms / death_quantize_ms) * death_quantize_ms)
    end_time = int(death_ms + pixel_fade_ms)
    return DissolveBandTiming(
        end_time=end_time,
        t_fade_on=max(0, death_ms - event_start),
        t_fade_off=max(1, end_time - event_start),
    )


def _clip_layer_to_band(
    layer: LayerShape,
    band_mask,
    band_bounds: tuple[float, float, float, float],
) -> MultiPolygon:
    if not _bounds_intersect(layer.bounds, band_bounds):
        return MultiPolygon()
    return _safe_intersection(layer.multipolygon, band_mask)


def _build_mask_piece_event(
    *,
    piece: MultiPolygon,
    layer: LayerShape,
    line_layer_base: int,
    style_name: str,
    event_start: int,
    move_tag: str,
    target_alpha: str,
    timing: DissolveBandTiming,
    drawing_min_point_spacing: float,
    mask_preroll_ms: int,
    min_piece_area: float,
    mask_piece_blur: float,
) -> OutputEvent | None:
    if piece.area <= max(0.0, min_piece_area):
        return None

    drawing = _shape_to_ass_drawing(piece, drawing_min_point_spacing)
    if not drawing:
        return None

    fade_in_tag = (
        f"\\t(0,{mask_preroll_ms},\\1a{target_alpha})"
        if mask_preroll_ms > 0
        else f"\\1a{target_alpha}"
    )
    text = (
        f"{{\\p1{move_tag}\\1c{layer.color}\\1a&HFF&\\blur{_format_ass_number(max(0.0, mask_piece_blur))}"
        f"{fade_in_tag}"
        f"\\t({timing.t_fade_on},{timing.t_fade_off},\\1a&HFF&)}}{drawing}"
    )
    return OutputEvent(
        layer=line_layer_base + layer.layer_offset,
        style=style_name,
        start_time=int(event_start),
        end_time=timing.end_time,
        text=text,
    )


def _apply_compression_preset(config: MeltConfig) -> MeltConfig:
    preset_name = config.compression_preset.strip().lower()
    if preset_name in {"", "0", "none", "off", "false"}:
        return config

    defaults = MeltConfig()
    presets: dict[str, dict[str, Any]] = {
        "high": {
            "mask_steps": 24,
            "mask_min_piece_area": 0.7,
            "mask_detail_points": 14,
            "death_quantize_ms": 60,
            "drawing_min_point_spacing": 1.0,
            "spike_total_count": 4,
            "spike_angle_cache_step": 5.0,
            "spike_scale_cache_precision": 1,
        },
        "extreme": {
            "mask_steps": 16,
            "mask_min_piece_area": 1.2,
            "mask_detail_points": 10,
            "death_quantize_ms": 100,
            "drawing_min_point_spacing": 1.35,
            "spike_total_count": 0,
            "spike_angle_cache_step": 10.0,
            "spike_scale_cache_precision": 0,
        },
    }

    preset = presets.get(preset_name)
    if preset is None:
        valid = ", ".join(sorted(presets))
        raise ValueError(
            f"Unknown compression preset: {config.compression_preset!r}. "
            f"Expected one of: {valid}."
        )

    return _apply_preset_overrides(config, defaults, preset)


def _apply_quality_preset(config: MeltConfig) -> MeltConfig:
    preset_name = config.quality_preset.strip().lower()
    if preset_name in {"", "default"}:
        preset_name = "quality"

    defaults = MeltConfig()
    presets: dict[str, dict[str, Any]] = {
        "quality": {
            "adaptive_mask_steps": False,
            "adaptive_mask_area_ref": 3600.0,
            "mask_steps": 32,
            "mask_detail_points": 28,
            "death_quantize_ms": 10,
            "drawing_min_point_spacing": 0.75,
            "mask_min_piece_area": 0.2,
            "output_coord_precision": 2,
            "merge_mask_bands_by_timing": True,
            "compression_preset": "none",
        },
        "balanced": {
            "adaptive_mask_steps": True,
            "adaptive_mask_area_ref": 4800.0,
            "mask_steps": 30,
            "mask_detail_points": 24,
            "death_quantize_ms": 15,
            "drawing_min_point_spacing": 0.8,
            "mask_min_piece_area": 0.3,
            "output_coord_precision": 2,
            "merge_mask_bands_by_timing": True,
            "compression_preset": "none",
        },
        "speed": {
            "adaptive_mask_steps": True,
            "adaptive_mask_area_ref": 6400.0,
            "mask_steps": 24,
            "mask_detail_points": 16,
            "death_quantize_ms": 30,
            "drawing_min_point_spacing": 1.0,
            "mask_min_piece_area": 0.7,
            "output_coord_precision": 1,
            "merge_mask_bands_by_timing": True,
            "compression_preset": "high",
        },
    }

    preset = presets.get(preset_name)
    if preset is None:
        valid = ", ".join(sorted(presets))
        raise ValueError(
            f"Unknown quality preset: {config.quality_preset!r}. "
            f"Expected one of: {valid}."
        )

    return _apply_preset_overrides(config, defaults, preset)


def _apply_preset_overrides(
    config: MeltConfig,
    defaults: MeltConfig,
    preset: dict[str, Any],
) -> MeltConfig:
    updates = {
        name: value
        for name, value in preset.items()
        if getattr(config, name) == getattr(defaults, name)
    }
    return replace(config, **updates)


def _random_spike_color(rng: random.Random) -> str:
    red = int(round(rng.uniform(0.0, 229.0)))
    return f"&H0000{red:02X}&"


def _resolve_death_quantize_ms(config: MeltConfig) -> int:
    if config.death_quantize_ms > 0:
        return config.death_quantize_ms
    return max(20, config.dissolve_duration // 40)


def _resolve_count_by_rate(window_ms: int, rate_per_100ms: float, fallback_count: int) -> int:
    rate = max(0.0, rate_per_100ms)
    if rate <= 0.0:
        return max(0, fallback_count)
    return max(0, int(round((max(0, window_ms) / 100.0) * rate)))


def _sample_front_loaded_time(start_ms: int, span_ms: int, rng: random.Random, *, bins: int = 28) -> int:
    if span_ms <= 1:
        return int(start_ms)

    sample_bins = max(8, bins)
    weights: list[float] = []
    for idx in range(sample_bins):
        u = (idx + 0.5) / sample_bins
        # Starts near zero, peaks in the front half, then tapers off.
        weight = (u**0.7) * ((1.0 - u) ** 2.2)
        weights.append(max(0.0, weight))

    total = sum(weights)
    if total <= 0.0:
        return int(start_ms + rng.uniform(0.0, span_ms))

    chosen = rng.choices(range(sample_bins), weights=weights, k=1)[0]
    bin_start = start_ms + (span_ms * chosen) / sample_bins
    bin_end = start_ms + (span_ms * (chosen + 1)) / sample_bins
    return int(rng.uniform(bin_start, max(bin_start + 1.0, bin_end)))


def _sample_triangular_emit_delay(span_ms: int, peak_t: float, rng: random.Random) -> int:
    if span_ms <= 1:
        return 0

    peak = max(0.001, min(0.999, peak_t))
    u = rng.random()
    if u <= peak:
        t = math.sqrt(u * peak)
    else:
        t = 1.0 - math.sqrt((1.0 - u) * (1.0 - peak))
    return int(round(t * span_ms))


def _sample_spike_velocity(
    *,
    base_angle_deg: float,
    speed: float,
    spherical_jitter: float,
    rng: random.Random,
) -> tuple[float, float, float]:
    cone_x = math.sin(math.radians(base_angle_deg))
    cone_y = -math.cos(math.radians(base_angle_deg))
    sphere_angle = rng.uniform(-180.0, 180.0)
    sphere_x = math.sin(math.radians(sphere_angle))
    sphere_y = -math.cos(math.radians(sphere_angle))
    mix = max(0.0, min(1.0, spherical_jitter))
    vel_x = ((1.0 - mix) * cone_x) + (mix * sphere_x)
    vel_y = ((1.0 - mix) * cone_y) + (mix * sphere_y)
    length = math.hypot(vel_x, vel_y)
    if length <= 0.0001:
        vel_x, vel_y = 0.0, -1.0
    else:
        vel_x /= length
        vel_y /= length
    return vel_x * speed, vel_y * speed, math.degrees(math.atan2(vel_y, vel_x))


def _format_ass_number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_ass_number_with_precision(value: float, precision: int) -> str:
    return f"{value:.{max(0, precision)}f}".rstrip("0").rstrip(".")


def _normalize_ass_alpha(alpha: str, fallback: str) -> str:
    raw = alpha.strip().upper()
    if raw.startswith("&H") and raw.endswith("&") and len(raw) == 5:
        return raw
    return fallback


def _build_motion_tag(
    *,
    event_start: int,
    syl_left: int,
    syl_top: int,
    motion_start_abs: int,
    motion_end_abs: int,
    motion_dx: float,
    motion_dy: float,
    motion_frz: float,
    output_coord_precision: int,
) -> str:
    motion_duration = max(1, motion_end_abs - motion_start_abs)
    precision = max(0, int(output_coord_precision))
    sx = _format_ass_number_with_precision(syl_left, precision)
    sy = _format_ass_number_with_precision(syl_top, precision)
    ex = _format_ass_number_with_precision(syl_left + motion_dx, precision)
    ey = _format_ass_number_with_precision(syl_top + motion_dy, precision)
    frz = _format_ass_number_with_precision(motion_frz, precision)

    if event_start >= motion_end_abs:
        return f"\\pos({ex},{ey})\\frz{frz}"

    if event_start <= motion_start_abs:
        t1 = max(0, motion_start_abs - event_start)
        t2 = max(t1 + 1, motion_end_abs - event_start)
        return f"\\move({sx},{sy},{ex},{ey},{t1},{t2})\\t({t1},{t2},\\frz{frz})"

    progressed = _smoothstep01((event_start - motion_start_abs) / motion_duration)
    cur_x = syl_left + motion_dx * progressed
    cur_y = syl_top + motion_dy * progressed
    cur_frz = motion_frz * progressed
    t2 = max(1, motion_end_abs - event_start)
    cx = _format_ass_number_with_precision(cur_x, precision)
    cy = _format_ass_number_with_precision(cur_y, precision)
    cfrz = _format_ass_number_with_precision(cur_frz, precision)
    return f"\\move({cx},{cy},{ex},{ey},0,{t2})\\frz{cfrz}\\t(0,{t2},\\frz{frz})"


def _resolve_effective_mask_steps(
    bounds: tuple[float, float, float, float],
    config: MeltConfig,
) -> int:
    base_steps = max(4, config.mask_steps)
    if not config.adaptive_mask_steps:
        return base_steps

    min_x, min_y, max_x, max_y = bounds
    area = max(1.0, (max_x - min_x) * (max_y - min_y))
    ref = max(1.0, float(config.adaptive_mask_area_ref))
    scale = max(0.35, min(1.0, math.sqrt(area / ref)))
    return max(6, int(round(base_steps * scale)))


def _collapse_band_masks_by_timing(
    *,
    band_masks: Sequence[Polygon],
    steps: int,
    dissolve_start: int,
    event_start: int,
    effective_window: int,
    pixel_fade_ms: int,
    death_quantize_ms: int,
    merge_by_timing: bool,
) -> list[tuple[MultiPolygon, DissolveBandTiming]]:
    collapsed: list[tuple[MultiPolygon, DissolveBandTiming]] = []
    if not band_masks:
        return collapsed

    if not merge_by_timing:
        for step_idx, band_mask in enumerate(band_masks):
            timing = _build_dissolve_band_timing(
                step_idx=step_idx,
                steps=steps,
                dissolve_start=dissolve_start,
                event_start=event_start,
                effective_window=effective_window,
                pixel_fade_ms=pixel_fade_ms,
                death_quantize_ms=death_quantize_ms,
            )
            collapsed.append((_repair_geometry(band_mask), timing))
        return collapsed

    current_timing: DissolveBandTiming | None = None
    current_masks: list[Polygon] = []

    for step_idx, band_mask in enumerate(band_masks):
        timing = _build_dissolve_band_timing(
            step_idx=step_idx,
            steps=steps,
            dissolve_start=dissolve_start,
            event_start=event_start,
            effective_window=effective_window,
            pixel_fade_ms=pixel_fade_ms,
            death_quantize_ms=death_quantize_ms,
        )

        if current_timing is None or timing != current_timing:
            if current_timing is not None and current_masks:
                merged = (
                    _repair_geometry(current_masks[0])
                    if len(current_masks) == 1
                    else _repair_geometry(unary_union(current_masks))
                )
                collapsed.append((merged, current_timing))
            current_timing = timing
            current_masks = [band_mask]
            continue

        current_masks.append(band_mask)

    if current_timing is not None and current_masks:
        merged = (
            _repair_geometry(current_masks[0])
            if len(current_masks) == 1
            else _repair_geometry(unary_union(current_masks))
        )
        collapsed.append((merged, current_timing))

    return collapsed


def _boost_ass_color(color: str, strength: float) -> str:
    raw = color.strip()
    if not (raw.startswith("&H") and raw.endswith("&") and len(raw) >= 8):
        return color

    hex_part = raw[2:-1]
    if len(hex_part) != 6:
        return color

    try:
        b = int(hex_part[0:2], 16)
        g = int(hex_part[2:4], 16)
        r = int(hex_part[4:6], 16)
    except ValueError:
        return color

    boost = max(0.0, min(1.0, strength))
    r2 = int(round(r + (255 - r) * boost))
    g2 = int(round(g + (255 - g) * boost))
    b2 = int(round(b + (255 - b) * boost))
    return f"&H{b2:02X}{g2:02X}{r2:02X}&"


def _get_cached_spike_tags(
    angle_deg: float,
    scale_x_factor: float,
    scale_y_factor: float,
    config: MeltConfig,
) -> str:
    angle_step = config.spike_angle_cache_step if config.spike_angle_cache_step > 0 else 1.0
    angle_key = round(angle_deg / angle_step) * angle_step
    scale_x_key = round(scale_x_factor, config.spike_scale_cache_precision)
    scale_y_key = round(scale_y_factor, config.spike_scale_cache_precision)
    cache_key = (float(angle_key), float(scale_x_key), float(scale_y_key))

    tag_str = _SPIKE_TAG_CACHE.get(cache_key)
    if tag_str is not None:
        return tag_str

    scale_x_percent = _format_ass_number(scale_x_key * 100.0)
    scale_y_percent = _format_ass_number(scale_y_key * 100.0)
    angle_text = _format_ass_number(-angle_key)
    tag_str = f"\\fscx{scale_x_percent}\\fscy{scale_y_percent}\\frz{angle_text}"
    _SPIKE_TAG_CACHE[cache_key] = tag_str
    return tag_str


def _build_spike_events(
    line,
    syl,
    unified: list[tuple[int, int]],
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
    rng: random.Random,
) -> list[OutputEvent]:
    syl_start, dissolve_start = _get_karaoke_syllable_times(line, syl)
    dissolve_end = dissolve_start + max(1, config.dissolve_duration)
    syl_left = float(syl.left)
    syl_top = float(syl.top)
    syl_width = max(1.0, float(getattr(syl, "width", 0.0) or 0.0))
    syl_height = max(1.0, float(getattr(syl, "height", 0.0) or 0.0))
    center_x = syl_left + (syl_width * 0.5)
    center_y = syl_top + (syl_height * 0.5)
    spawn_radius = max(4.0, min(syl_width, syl_height) * max(0.05, config.spike_spawn_radius_factor))
    clamp_margin = max(0.0, config.spike_bound_margin)
    precision = max(0, int(config.output_coord_precision))
    points = [(syl_left + px, syl_top + py) for px, py in unified] if unified else []
    events: list[OutputEvent] = []

    def _emit_spike_cluster(
        *,
        emit_start: int,
        emit_span: int,
        count: int,
        bound_margin: float,
        cone_scale: float = 1.0,
        travel_scale: float = 1.0,
        radial_scale: float = 1.0,
        lifetime_scale: float = 1.0,
        spawn_radius_scale: float = 1.0,
        blur: float = 1.0,
        scale_y_boost: float = 1.0,
    ) -> None:
        if count <= 0:
            return

        bound_min_x = syl_left - bound_margin
        bound_max_x = syl_left + syl_width + bound_margin
        bound_min_y = syl_top - bound_margin
        bound_max_y = syl_top + syl_height + bound_margin
        local_emit_span = max(1, emit_span)

        for _ in range(count):
            if points and rng.random() < 0.2:
                sx, sy = points[rng.randrange(len(points))]
            else:
                spawn_angle = rng.uniform(0.0, math.tau)
                spawn_dist = math.sqrt(rng.random()) * spawn_radius * spawn_radius_scale
                sx = center_x + (math.cos(spawn_angle) * spawn_dist)
                sy = center_y + (math.sin(spawn_angle) * spawn_dist)

            jitter = config.spike_spawn_jitter
            sx += rng.uniform(-jitter, jitter)
            sy += rng.uniform(-jitter, jitter)

            birth_time = emit_start + _sample_triangular_emit_delay(
                local_emit_span,
                config.spike_emit_peak_t,
                rng,
            )

            life_min = max(40, int(config.spike_lifetime_min_ms * lifetime_scale))
            life_max = max(life_min, int(config.spike_lifetime_max_ms * lifetime_scale))
            lifetime = int(rng.uniform(life_min, life_max))
            death_time = int(birth_time + lifetime)

            cone_half = max(1.0, config.spike_cone_half_angle * cone_scale)
            base_angle = rng.uniform(-cone_half, cone_half)
            speed = rng.uniform(config.spike_speed_min, config.spike_speed_max)
            vel_x, vel_y, draw_angle = _sample_spike_velocity(
                base_angle_deg=base_angle,
                speed=speed,
                spherical_jitter=config.spike_spherical_jitter,
                rng=rng,
            )

            radial_dx = sx - center_x
            radial_dy = sy - center_y
            radial_len = math.hypot(radial_dx, radial_dy)
            if radial_len > 0.0001:
                vel_x += (radial_dx / radial_len) * config.spike_radial_speed * radial_scale
                vel_y += (radial_dy / radial_len) * config.spike_radial_speed * radial_scale

            # Aegisub uses the opposite sign convention for \frz, so keep the
            # final screen-space movement angle here and negate only when writing tags.
            draw_angle = math.degrees(math.atan2(vel_y, vel_x))
            travel = lifetime * 0.001 * travel_scale
            x2 = sx + (vel_x * travel)
            y2 = sy + (vel_y * travel)
            x2 = min(bound_max_x, max(bound_min_x, x2))
            y2 = min(bound_max_y, max(bound_min_y, y2))

            target_scale_y = rng.uniform(config.spike_scale_y_min, config.spike_scale_y_max) * scale_y_boost
            target_scale_x = (
                rng.uniform(config.spike_scale_x_min, config.spike_scale_x_max)
                * max(1.0, config.spike_stretch_percent / 100.0)
            )
            start_scale_x = max(0.1, target_scale_x * 0.08)
            start_scale_y = max(0.08, target_scale_y * 0.08)
            size_peak_ms = max(1, int(lifetime * config.spike_size_peak_t))
            alpha_peak_ms = max(1, int(lifetime * config.spike_alpha_peak_t))
            spike_color = _random_spike_color(rng)
            blur_text = _format_ass_number(blur)
            start_tags = (
                f"\\fscx{_format_ass_number(start_scale_x * 100.0)}"
                f"\\fscy{_format_ass_number(start_scale_y * 100.0)}"
                f"\\frz{_format_ass_number(-draw_angle)}"
            )
            target_scale_tags = (
                f"\\fscx{_format_ass_number(target_scale_x * 100.0)}"
                f"\\fscy{_format_ass_number(target_scale_y * 100.0)}"
            )
            move_tag = (
                f"\\an5\\move({_format_ass_number_with_precision(sx, precision)},{_format_ass_number_with_precision(sy, precision)},"
                f"{_format_ass_number_with_precision(x2, precision)},{_format_ass_number_with_precision(y2, precision)})"
            )
            glow_inner_alpha = _normalize_ass_alpha(config.spike_glow_inner_alpha, "&H78&")
            glow_outer_alpha = _normalize_ass_alpha(config.spike_glow_outer_alpha, "&HAC&")

            if config.spike_glow_enabled:
                outer_start_tags = (
                    f"\\fscx{_format_ass_number(start_scale_x * config.spike_glow_outer_scale * 100.0)}"
                    f"\\fscy{_format_ass_number(start_scale_y * config.spike_glow_outer_scale * 100.0)}"
                    f"\\frz{_format_ass_number(-draw_angle)}"
                )
                outer_target_tags = (
                    f"\\fscx{_format_ass_number(target_scale_x * config.spike_glow_outer_scale * 100.0)}"
                    f"\\fscy{_format_ass_number(target_scale_y * config.spike_glow_outer_scale * 100.0)}"
                )
                outer_text = (
                    f"{{{move_tag}\\p1\\bord0\\shad0\\blur{_format_ass_number(config.spike_glow_outer_blur)}"
                    f"\\1c{spike_color}\\alpha&HFF&{outer_start_tags}"
                    f"\\t(0,{size_peak_ms},{outer_target_tags})"
                    f"\\t(0,{alpha_peak_ms},\\alpha{glow_outer_alpha})"
                    f"\\t({alpha_peak_ms},{lifetime},\\alpha&HFF&)}}{SPIKE_BASE_DRAWING}"
                )
                events.append(
                    OutputEvent(
                        layer=line_layer_base,
                        style=style_name,
                        start_time=birth_time,
                        end_time=death_time,
                        text=outer_text,
                    )
                )

                inner_start_tags = (
                    f"\\fscx{_format_ass_number(start_scale_x * config.spike_glow_inner_scale * 100.0)}"
                    f"\\fscy{_format_ass_number(start_scale_y * config.spike_glow_inner_scale * 100.0)}"
                    f"\\frz{_format_ass_number(-draw_angle)}"
                )
                inner_target_tags = (
                    f"\\fscx{_format_ass_number(target_scale_x * config.spike_glow_inner_scale * 100.0)}"
                    f"\\fscy{_format_ass_number(target_scale_y * config.spike_glow_inner_scale * 100.0)}"
                )
                inner_text = (
                    f"{{{move_tag}\\p1\\bord0\\shad0\\blur{_format_ass_number(config.spike_glow_inner_blur)}"
                    f"\\1c{spike_color}\\alpha&HFF&{inner_start_tags}"
                    f"\\t(0,{size_peak_ms},{inner_target_tags})"
                    f"\\t(0,{alpha_peak_ms},\\alpha{glow_inner_alpha})"
                    f"\\t({alpha_peak_ms},{lifetime},\\alpha&HFF&)}}{SPIKE_BASE_DRAWING}"
                )
                events.append(
                    OutputEvent(
                        layer=line_layer_base + 1,
                        style=style_name,
                        start_time=birth_time,
                        end_time=death_time,
                        text=inner_text,
                    )
                )

            text = (
                f"{{{move_tag}\\p1\\bord0\\shad0\\blur{blur_text}\\1c{spike_color}\\alpha&HFF&{start_tags}"
                f"\\t(0,{size_peak_ms},{target_scale_tags})"
                f"\\t(0,{alpha_peak_ms},\\alpha&H00&)"
                f"\\t({alpha_peak_ms},{lifetime},\\alpha&HFF&)}}{SPIKE_BASE_DRAWING}"
            )
            events.append(
                OutputEvent(
                    layer=line_layer_base + 2,
                    style=style_name,
                    start_time=birth_time,
                    end_time=death_time,
                    text=text,
                )
            )

    spike_lead_ms = max(0, config.spike_lead_ms)
    regular_emit_start = max(syl_start, dissolve_start - max(0, config.spike_early_start_ms) - spike_lead_ms)
    regular_emit_end = max(
        regular_emit_start + 1,
        dissolve_start - spike_lead_ms + int(config.dissolve_duration * 0.65),
    )
    regular_emit_span = max(1, regular_emit_end - regular_emit_start)
    regular_count = _resolve_count_by_rate(
        regular_emit_span,
        config.spike_count_per_100ms,
        config.spike_total_count,
    )
    regular_count = max(max(0, config.spike_min_count), regular_count)
    _emit_spike_cluster(
        emit_start=regular_emit_start,
        emit_span=regular_emit_span,
        count=regular_count,
        bound_margin=clamp_margin,
    )

    burst_window = max(20, config.predissolve_spike_window_ms)
    predissolve_count = _resolve_count_by_rate(
        burst_window,
        config.predissolve_spike_count_per_100ms,
        config.predissolve_spike_count,
    )
    predissolve_count = max(max(0, config.predissolve_spike_min_count), predissolve_count)

    if predissolve_count > 0:
        burst_advance = max(0, config.predissolve_spike_start_advance_ms)
        burst_start = max(syl_start, dissolve_end - burst_window - burst_advance - spike_lead_ms)
        burst_end = burst_start + burst_window
        burst_span = max(1, burst_end - burst_start)
        _emit_spike_cluster(
            emit_start=burst_start,
            emit_span=burst_span,
            count=predissolve_count,
            bound_margin=clamp_margin + max(0.0, config.predissolve_spike_bound_extra),
            cone_scale=max(1.0, config.predissolve_spike_angle_multiplier),
            travel_scale=max(1.0, config.predissolve_spike_travel_multiplier),
            radial_scale=max(1.0, config.predissolve_spike_accel * 0.55),
            lifetime_scale=max(0.7, config.predissolve_spike_lifetime_ms / max(1, config.spike_lifetime_max_ms)),
            spawn_radius_scale=1.2,
            blur=0.6,
            scale_y_boost=max(1.0, config.predissolve_spike_scale_y),
        )

    return events


def melt_line(
    line,
    line_layer_base: int,
    config: MeltConfig,
    seed: int,
    style_name: str = "p",
) -> list[OutputEvent]:
    rng = random.Random(seed)
    events: list[OutputEvent] = []
    syllables = Utils.all_non_empty(line.syls, progress_bar=False)

    for syl in syllables:
        layer_shapes = text_to_layer_shapes(syl)
        if not layer_shapes:
            continue

        events.extend(
            _build_full_shape_events(
                line=line,
                syl=syl,
                layers=layer_shapes,
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
            )
        )
        events.extend(
            _build_vector_mask_events(
                line=line,
                syl=syl,
                layers=layer_shapes,
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
                rng=rng,
            )
        )
        spike_points = _collect_shape_points(layer_shapes)
        events.extend(
            _build_spike_events(
                line=line,
                syl=syl,
                unified=spike_points,
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
                rng=rng,
            )
        )

    return events


# Rendering pipeline
def _process_line_worker(args: tuple[Line, int, MeltConfig, int, str]) -> list[OutputEvent]:
    line, line_layer_base, config, seed, style_name = args
    return melt_line(line, line_layer_base, config, seed, style_name=style_name)


def _write_output_events(io: Ass, template_line: Line, events: list[OutputEvent]) -> None:
    if not events:
        return

    event_kind = "Comment" if template_line.comment else "Dialogue"
    actor = template_line.actor
    margin_l = f"{template_line.margin_l:04d}"
    margin_r = f"{template_line.margin_r:04d}"
    margin_v = f"{template_line.margin_v:04d}"
    effect = template_line.effect
    time_cache: dict[int, str] = {}

    serialized: list[str] = []
    append_serialized = serialized.append
    for event in events:
        start_ms = max(0, int(event.start_time))
        end_ms = max(0, int(event.end_time))

        start_text = time_cache.get(start_ms)
        if start_text is None:
            start_text = Convert.time(start_ms)
            time_cache[start_ms] = start_text

        end_text = time_cache.get(end_ms)
        if end_text is None:
            end_text = Convert.time(end_ms)
            time_cache[end_ms] = end_text

        append_serialized(
            f"{event_kind}: {event.layer},{start_text},{end_text},{event.style},{actor},"
            f"{margin_l},{margin_r},{margin_v},{effect},{event.text}\n"
        )

    io._output.extend(serialized)
    io._plines += len(serialized)


def _iter_target_lines(lines: list[Line]) -> list[tuple[int, Line]]:
    result: list[tuple[int, Line]] = []
    line_index = 0
    for line in lines:
        if line.comment or line.styleref.alignment > 3:
            continue
        result.append((line_index, line))
        line_index += 1
    return result


def _should_use_multiprocessing(target_lines: list[tuple[int, Line]], config: MeltConfig) -> bool:
    return (
        config.enable_multiprocessing
        and len(target_lines) >= config.multiprocessing_min_lines
    )


# CLI helpers
def _parse_bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the spike melt effect into an ASS file.")
    parser.add_argument("--input", default="in.ass", help="Input ASS path.")
    parser.add_argument("--output", default="output.ass", help="Output ASS path.")
    parser.add_argument("--style-name", default="p", help="Generated effect style name.")
    parser.add_argument("--keep-original", type=_parse_bool_arg, default=True, help="Whether to comment and preserve original dialogue lines.")
    parser.add_argument("--extended", type=_parse_bool_arg, default=True, help="Whether Ass should compute extended line data.")
    parser.add_argument("--quality-preset", default=None, choices=("quality", "balanced", "speed"), help="High-level quality/speed preset.")
    parser.add_argument(
        "--line-dual-glow",
        dest="line_dual_glow_enabled",
        action="store_true",
        default=None,
        help="Enable dual glow for base subtitle layers.",
    )
    parser.add_argument(
        "--no-line-dual-glow",
        dest="line_dual_glow_enabled",
        action="store_false",
        help="Disable dual glow for base subtitle layers.",
    )

    for config_field in fields(MeltConfig):
        if config_field.name in {"quality_preset", "line_dual_glow_enabled"}:
            continue
        parser.add_argument(
            f"--{config_field.name.replace('_', '-')}",
            dest=config_field.name,
            type=_parse_bool_arg if isinstance(config_field.default, bool) else type(config_field.default),
            default=None,
            help=f"Override MeltConfig.{config_field.name} (default: {config_field.default!r}).",
        )

    return parser


def build_config_from_args(args: argparse.Namespace) -> MeltConfig:
    overrides = {
        config_field.name: value
        for config_field in fields(MeltConfig)
        if (value := getattr(args, config_field.name)) is not None
    }
    if args.quality_preset is not None:
        overrides["quality_preset"] = args.quality_preset
    return MeltConfig(**overrides)


def _configure_stdio_for_windows() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (LookupError, ValueError, OSError):
                pass


def render_spike(
    input_path: str = "in.ass",
    output_path: str = "output.ass",
    *,
    config: MeltConfig | None = None,
    style_name: str = "p",
    keep_original: bool = True,
    extended: bool = True,
) -> str:
    _configure_stdio_for_windows()
    config = _apply_quality_preset(config or MeltConfig())
    config = _apply_compression_preset(config)
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)
    io = Ass(input_path, output_path, keep_original=keep_original, extended=extended)
    _, _, lines = io.get_data()
    io.add_style(style_name, Ass.PIXEL_STYLE)

    target_lines = _iter_target_lines(lines)
    if not target_lines:
        io.save()
        return io.path_output

    work_items = [
        (
            line.copy(),
            line_index * LAYERS_PER_LINE,
            config,
            config.random_seed + line.i,
            style_name,
        )
        for line_index, line in target_lines
    ]

    if _should_use_multiprocessing(target_lines, config):
        ctx = mp.get_context("spawn")
        workers = config.max_workers if config.max_workers > 0 else mp.cpu_count()
        with ctx.Pool(processes=workers) as pool:
            results = pool.map(_process_line_worker, work_items)
    else:
        results = [_process_line_worker(item) for item in work_items]

    for (_, line), events in zip(target_lines, results):
        _write_output_events(io, line, events)

    io.save()
    return io.path_output


def main(argv: Sequence[str] | None = None) -> str:
    _configure_stdio_for_windows()
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = build_config_from_args(args)
    return render_spike(
        input_path=args.input,
        output_path=args.output,
        config=config,
        style_name=args.style_name,
        keep_original=args.keep_original,
        extended=args.extended,
    )


if __name__ == "__main__":
    main()
