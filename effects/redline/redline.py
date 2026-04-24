from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import os
import random
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields, replace
from types import SimpleNamespace

from pyonfx import Ass, Line, Utils
from word.word_fx_adapter import create_word_fx_bridge, shift_word_fx_line


LAYERS_PER_LINE = 5
MAX_RENDER_SLICES = 200
FLOAT_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class MeltConfig:
    line_lead_in_ms: int = 640
    line_fade_in_ms: int = 90
    line_highlight_ms: int = 70
    line_pop_ms: int = 140
    line_long_hold_threshold_ms: int = 240
    line_long_hold_tail_ms: int = 520

    enable_multiprocessing: bool = True
    multiprocessing_min_lines: int = 6
    max_workers: int = 0
    random_seed: int = 24681357

    quality_preset: str = "quality"

    anim_fps: float = 23.976 / 2
    min_slice_ms: int = 40

    line_extend_px: float = 12.0
    line_width_px: float = 4.2
    line_width_jitter_ratio: float = 0.06
    curve_arc_px: float = 18.0
    curve_wave_amp_px: float = 10.5
    curve_wave_cycles: float = 2.2
    curve_wave_speed_hz: float = 0.9
    curve_peak_skew: float = 0.32
    underline_offset_px: float = 0.0
    sample_density_px: float = 10.0
    sample_min_points: int = 18
    sample_max_points: int = 64

    static_amp_px: float = 0.35
    static_cycles: float = 1.5
    flow_amp_px: float = 0.6
    flow_cycles: float = 1.2
    flow_speed_hz: float = 0.22
    boil_amp_px: float = 0.15
    boil_cycles: float = 1.6

    spark_count_min: int = 3
    spark_count_max: int = 3
    spark_len_min_px: float = 12.0
    spark_len_max_px: float = 28.0
    spark_follow_smooth_x: float = 0.72
    spark_follow_smooth_y: float = 0.86
    spark_jitter_amp_px: float = 2.2
    spark_drift_px: float = 12.0
    spark_spread_deg_min: float = 120.0
    spark_spread_deg_max: float = 240.0
    spark_prefade_lead_ms: float = 200.0
    spark_tail_fade_ms: float = 180.0
    tail_knot_enabled: bool = True
    tail_knot_radius_px: float = 14.0
    tail_knot_extend_px: float = 30.0
    tail_knot_wipe_speed: float = 1.5

    butterfly_duration_ms: int = 860
    butterfly_fade_ms: int = 180
    wing_cycle_fps: int = 8
    butterfly_dx_px: float = 64.0
    butterfly_dy_px: float = -52.0
    butterfly_arc_px: float = 26.0
    butterfly_scale: float = 1.0
    butterfly_min_syllable_gap: int = 2
    butterfly_max_syllable_gap: int = 5
    butterfly_gap_probability_step: float = 0.3
    butterfly_long_hold_interval_ms: int = 520
    butterfly_long_hold_max_extra: int = 6
    butterfly_spawn_jitter_x_px: float = 34.0
    butterfly_spawn_jitter_up_px: float = 40.0
    butterfly_direction_min_deg: float = -170.0
    butterfly_direction_max_deg: float = -10.0
    butterfly_forward_angle_offset_deg: float = 90.0
    butterfly_frame_turn_min_deg: float = 5.0
    butterfly_frame_turn_max_deg: float = 10.0
    butterfly_turn_bound_deg: float = 24.0

    line_color_main: str = "&H2D2DCC&"
    line_color_soft: str = "&H3A3AE0&"
    spark_color: str = "&H4A4AFF&"
    butterfly_color: str = "&H5A5AFF&"
    line_alpha_main: str = "&H10&"
    line_alpha_soft: str = "&H40&"
    spark_alpha: str = "&H30&"
    butterfly_alpha: str = "&H08&"
    butterfly_glow_alpha: str = "&H28&"
    butterfly_glow_blur: float = 6.0
    butterfly_glow_border_px: float = 2.0
    soft_blur: float = 0.9

    mask_edge_jitter_px: float = 2.0


@dataclass(frozen=True, slots=True)
class OutputEvent:
    layer: int
    style: str
    start_time: float
    end_time: float
    text: str


@dataclass(frozen=True, slots=True)
class LayerShape:
    """Describe one material layer for a generated drawing."""

    drawing: str
    color: str
    alpha: str
    layer_offset: int
    blur: float = 0.0


@dataclass(frozen=True, slots=True)
class CenterPoint:
    """Store a sampled centerline point."""

    x: float
    y: float
    s: float


@dataclass(frozen=True, slots=True)
class RibbonPath:
    """Store left and right ribbon boundaries."""

    left: list[tuple[float, float]]
    right: list[tuple[float, float]]


@dataclass(frozen=True, slots=True)
class SparkSlash:
    """Store one spark slash definition."""

    seed: int
    anchor_x: float
    anchor_y: float
    angle_deg: float
    length: float
    anchor_offset_x: float
    anchor_offset_y: float
    angle_wobble_deg: float
    length_wobble_px: float
    phase_x: float
    phase_y: float
    phase_angle: float
    phase_length: float


@dataclass(frozen=True, slots=True)
class ButterflyFrame:
    """Store one butterfly drawing frame."""

    frame_index: int
    drawing: str
    scale: int = 1
    bounds: tuple[int, int, int, int] | None = None


BUTTERFLY_FRAME_SOURCE = os.path.join("butterfly", "output", "butterfly.10frames.0.2s.ass")
BUTTERFLY_DRAWING_RE = re.compile(r"\\p(?P<scale>\d+)\}(?P<drawing>.*?)(?:\{\\p0\}|$)")
_BUTTERFLY_FRAME_CACHE: tuple[ButterflyFrame, ...] | None = None


@dataclass(frozen=True, slots=True)
class SylContext:
    """Bundle repeated per-syllable arguments."""

    line: Line
    syl: object
    seed: int
    config: MeltConfig


@dataclass(frozen=True, slots=True)
class HarmonicProfile:
    """Describe one preset harmonic contour layered onto the main curve."""

    amplitude_ratio: float
    frequency: float
    speed_hz: float
    phase_channel: int
    rise_left: float
    rise_right: float
    fall_left: float
    fall_right: float
    skew: float
    use_cosine: bool = False


HARMONIC_PROFILES: tuple[HarmonicProfile, ...] = (
    HarmonicProfile(0.16, 1.25, 0.28, 41, 0.00, 0.18, 0.64, 1.00, -0.26, False),
    HarmonicProfile(0.14, 2.05, 0.34, 42, 0.02, 0.22, 0.82, 0.98, 0.32, True),
    HarmonicProfile(0.18, 2.90, 0.22, 43, 0.10, 0.28, 0.70, 0.88, -0.42, False),
    HarmonicProfile(0.13, 3.55, 0.41, 44, 0.18, 0.38, 0.64, 0.82, 0.40, True),
    HarmonicProfile(0.10, 4.35, 0.30, 45, 0.08, 0.22, 0.48, 0.68, -0.30, False),
    HarmonicProfile(0.12, 1.65, 0.18, 46, 0.38, 0.50, 0.88, 1.00, 0.22, True),
    HarmonicProfile(0.15, 2.45, 0.26, 47, 0.46, 0.58, 0.94, 1.00, -0.36, False),
)


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Clamp value to the provided range."""

    return max(minimum, min(maximum, value))


def lerp(a: float, b: float, t: float) -> float:
    """Linearly interpolate between a and b."""

    return a + (b - a) * t


def smoothstep(edge0: float, edge1: float, x: float) -> float:
    """Return a GLSL-like smoothstep with support for reversed edges."""

    if math.isclose(edge0, edge1, abs_tol=FLOAT_EPSILON):
        return 0.0 if x < edge0 else 1.0
    t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _round_point(point: tuple[float, float]) -> tuple[int, int]:
    """Round a point to integer ASS coordinates."""

    return (round(point[0]), round(point[1]))


def _format_drawing(points: list[tuple[float, float]]) -> str:
    """Serialize a closed polygon into ASS drawing syntax."""

    if len(points) < 3:
        return ""

    rounded = [_round_point(point) for point in points]
    if rounded[0] != rounded[-1]:
        rounded.append(rounded[0])
    commands = [f"m {rounded[0][0]} {rounded[0][1]}"]
    commands.extend(f"l {x} {y}" for x, y in rounded[1:])
    return " ".join(commands)


def _drawing_bounds(drawing: str) -> tuple[int, int, int, int] | None:
    """Return min/max bounds for a drawing string."""

    tokens = drawing.split()
    coords: list[int] = []
    for token in tokens:
        try:
            coords.append(int(token))
        except ValueError:
            continue
    if len(coords) < 4:
        return None
    xs = coords[0::2]
    ys = coords[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _drawing_to_text(
    drawing: str,
    *,
    color: str,
    alpha: str,
    blur: float,
    extra_tags: str = "",
) -> str:
    """Build ASS override tags for a vector drawing."""

    blur_tag = f"\\blur{blur:g}" if blur > 0 else ""
    return f"{{\\an7\\pos(0,0)\\bord0\\1c{color}\\1a{alpha}{blur_tag}{extra_tags}\\p1}}{drawing}"


def _noise_seed(seed: int, key: int) -> random.Random:
    """Create a deterministic RNG for a derived key."""

    return random.Random(seed * 1_000_003 + key * 97_531)


def hash_noise(s: float, k: int, seed: int) -> float:
    """Return deterministic noise in [-1, 1]."""

    bucket = int(round(s * 1000.0))
    return _noise_seed(seed, bucket + k * 8191).uniform(-1.0, 1.0)


def _fract(value: float) -> float:
    """Return the fractional part of a float."""

    return value - math.floor(value)


def _hash_2d(ix: int, iy: int, seed: int = 0) -> float:
    """Hash integer grid coordinates to [0, 1)."""

    n = ix * 127.1 + iy * 311.7 + seed * 74.7
    return _fract(math.sin(n) * 43758.5453123)


def value_noise_2d(x: float, y: float, seed: int = 0) -> float:
    """Return smooth value noise in [-1, 1]."""

    x0 = math.floor(x)
    y0 = math.floor(y)
    tx = smoothstep(0.0, 1.0, x - x0)
    ty = smoothstep(0.0, 1.0, y - y0)

    a = _hash_2d(x0, y0, seed)
    b = _hash_2d(x0 + 1, y0, seed)
    c = _hash_2d(x0, y0 + 1, seed)
    d = _hash_2d(x0 + 1, y0 + 1, seed)
    ab = a + (b - a) * tx
    cd = c + (d - c) * tx
    return (ab + (cd - ab) * ty) * 2.0 - 1.0


def fbm(x: float, y: float, seed: int = 0, octaves: int = 3) -> float:
    """Return fractal Brownian motion based on value noise."""

    value = 0.0
    amp = 0.5
    freq = 1.0
    total = 0.0
    for octave in range(octaves):
        value += value_noise_2d(x * freq, y * freq, seed + octave * 17) * amp
        total += amp
        amp *= 0.5
        freq *= 2.03
    return value / total if total else 0.0


def _phase_from_seed(seed: int, channel: int) -> float:
    """Return a deterministic phase offset."""

    return _noise_seed(seed, channel).uniform(0.0, math.tau)


def _syl_time_to_absolute(line: Line, syl_time_ms: float) -> float:
    """Convert syllable time to absolute timeline time when needed."""

    line_start = float(getattr(line, "start_time", 0.0))
    line_end = float(getattr(line, "end_time", line_start))
    line_duration = max(0.0, line_end - line_start)
    # PyonFX syllables are usually line-relative, but some internal proxies in this
    # script already use absolute times. Treat values as relative only when they are
    # clearly inside the line-local 0..duration range and still before line_start.
    if line_start > 1.0 and 0.0 <= syl_time_ms <= line_duration + 1.0 and syl_time_ms < line_start - 1.0:
        return line_start + syl_time_ms
    return syl_time_ms


def _effective_stroke_end_time(line: Line, syllables: Sequence[object], config: MeltConfig) -> float:
    """End main stroke near karaoke timing, while allowing a short long-note tail."""

    line_end = float(getattr(line, "end_time", 0.0))
    if not syllables:
        return line_end

    last_kf_end = max(_syl_time_to_absolute(line, syl.end_time) for syl in syllables)
    trailing_hold = line_end - last_kf_end
    if trailing_hold <= config.line_long_hold_threshold_ms:
        return line_end
    return min(line_end, last_kf_end + config.line_long_hold_tail_ms)


def _normalize(dx: float, dy: float) -> tuple[float, float]:
    """Normalize a vector with a safe fallback."""

    length = math.hypot(dx, dy)
    if length <= FLOAT_EPSILON:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _harmonic_offset(s: float, x_norm: float, t_sec: float, ctx: SylContext) -> float:
    """Add a few seeded harmonic presets to break up overly regular curvature."""

    rng = random.Random(ctx.seed + 4_211)
    selected = rng.sample(HARMONIC_PROFILES, k=min(3, len(HARMONIC_PROFILES)))
    total = 0.0
    for index, profile in enumerate(selected):
        amp_jitter = rng.uniform(0.92, 1.18)
        freq_jitter = rng.uniform(0.90, 1.14)
        speed_jitter = rng.uniform(0.92, 1.10)
        x_warp = rng.uniform(0.52, 0.92)
        phase_bias = rng.uniform(-0.35, 0.35)
        skew_jitter = profile.skew + rng.uniform(-0.18, 0.18)
        s_skewed = clamp(s + skew_jitter * (1.0 - x_norm * x_norm), 0.0, 1.0)
        env = smoothstep(profile.rise_left, profile.rise_right, s_skewed) * smoothstep(
            profile.fall_right, profile.fall_left, s_skewed
        )
        env = env**0.92
        phase = _phase_from_seed(ctx.seed, profile.phase_channel)
        argument = (
            math.tau * (profile.frequency * freq_jitter * s_skewed - profile.speed_hz * speed_jitter * t_sec)
            + phase
            + phase_bias
            + x_norm * x_warp
        )
        trig_base = math.cos(argument) if profile.use_cosine else math.sin(argument)
        trig_soft = math.sin(argument * rng.uniform(1.12, 1.45) + phase * 0.18)
        trig = clamp(trig_base * 0.88 + trig_soft * 0.18, -1.0, 1.0)
        total += ctx.config.curve_arc_px * profile.amplitude_ratio * amp_jitter * env * trig

        if index == 0:
            kink_env = smoothstep(max(0.0, profile.rise_left - 0.06), profile.rise_right, s_skewed) * smoothstep(
                profile.fall_right, min(1.0, profile.fall_left + 0.08), s_skewed
            )
            kink = math.sin(argument * rng.uniform(1.55, 2.05) - phase_bias) * rng.uniform(0.02, 0.05)
            total += ctx.config.curve_arc_px * kink_env * kink
    return total


def _sample_centerline(ctx: SylContext) -> list[CenterPoint]:
    """Build a straight centerline scaffold for the syllable stroke."""

    syl = ctx.syl
    config = ctx.config
    x_left = syl.left - config.line_extend_px
    x_right = syl.right + config.line_extend_px
    span = x_right - x_left
    if span < 2:
        return []

    n = int(clamp(round(span / config.sample_density_px), config.sample_min_points, config.sample_max_points))
    if n < 2:
        return []

    y_anchor = getattr(ctx.line, "middle", getattr(syl, "middle", getattr(syl, "bottom", 0.0)))
    y_base = y_anchor + config.underline_offset_px
    points: list[CenterPoint] = []
    for i in range(n):
        s = 0.0 if n == 1 else i / (n - 1)
        x = lerp(x_left, x_right, s)
        points.append(CenterPoint(x=x, y=y_base, s=s))
    return points


def _apply_curve_profile(base_points: list[CenterPoint], ctx: SylContext, time_ms: int) -> list[CenterPoint]:
    """Apply the shader-like curve profile while preserving the current stroke system."""

    if len(base_points) < 2:
        return []

    config = ctx.config
    t_sec = time_ms / 1000.0
    phase_main = _phase_from_seed(ctx.seed, 31)
    phase_detail = _phase_from_seed(ctx.seed, 32)
    phase_endpoint = _phase_from_seed(ctx.seed, 33)
    curved: list[CenterPoint] = []

    # Tuned from the current validator panel values.
    ui_amplitude = 80.0
    ui_speed = 1.0
    ui_spatial_frequency = 3.0
    ui_detail_strength = 0.35
    ui_inertia = 0.18
    ui_active_region_strength = 1.0
    ui_frame_skip = 4

    # Slightly smooth only the second subtitle line.
    if int(getattr(ctx.line, "i", -1)) == 1:
        ui_spatial_frequency *= 0.92
        ui_detail_strength *= 0.82
        ui_amplitude *= 0.95

    hold_step_sec = max(1.0 / max(1.0, config.anim_fps), 1e-3) * ui_frame_skip
    detail_time_sec = math.floor(t_sec / hold_step_sec) * hold_step_sec
    wind_time = t_sec * ui_speed * (0.34 + 0.22 * ui_inertia)
    phase = wind_time * math.tau + phase_main * 0.12
    phase_jitter = fbm(wind_time * 0.11 + 5.0 + phase_detail, 0.0, 141 + ctx.seed % 17, 3)
    shape_mix = 0.5 + 0.5 * fbm(wind_time * 0.09 + 2.0 + phase_main, 1.0, 142 + ctx.seed % 19, 3)
    global_push = value_noise_2d(wind_time * 0.18 + 17.0 + phase_main * 0.3, 0.0, 121)
    gust_center = 0.5 + 0.28 * math.sin(wind_time * 0.43 + 1.8 + phase_main)
    gust_center += 0.12 * value_noise_2d(wind_time * 0.16 + 4.0 + phase_detail * 0.2, 2.0, 122)
    gust_center = clamp(gust_center, 0.08, 0.92)
    gust_width = 0.30 + 0.07 * math.sin(wind_time * 0.31 + 0.4 + phase_detail * 0.1)
    arc_bias = config.curve_arc_px * 0.42
    wave_px_scale = config.curve_arc_px / 18.0

    for point in base_points:
        x_norm = lerp(-1.0, 1.0, point.s)
        distance = abs(point.s - gust_center)
        gust = math.exp(-((distance / max(0.12, gust_width)) ** 2)) * ui_active_region_strength
        edge_fade = math.sin(math.pi * point.s)

        local_warp = fbm(
            point.s * (ui_spatial_frequency * 1.6) + phase_jitter * 1.9,
            wind_time * 0.24 + 3.0,
            143 + ctx.seed % 13,
            2,
        )
        advected_u = point.s * ui_spatial_frequency - wind_time
        advected_u += local_warp * 0.18 + phase_jitter * 0.09

        carrier = math.sin(advected_u * math.tau * 0.62 + phase * (0.14 + 0.08 * shape_mix))
        shoulder = math.sin(
            advected_u * math.tau * 1.08 - phase * (0.08 + 0.06 * (1.0 - shape_mix)) + 1.9
        )
        bow = math.sin(math.pi * point.s + global_push * 0.55)
        broad_noise = fbm(advected_u * 0.72 + 8.0, wind_time * (0.28 + 0.11 * shape_mix), 132, 2)
        smooth_detail = fbm(advected_u * 1.95 + 13.0, detail_time_sec * 0.52 + 4.1, 144, 2)

        body = carrier * (0.34 + 0.16 * shape_mix)
        body += shoulder * (0.11 + 0.13 * (1.0 - shape_mix))
        body += broad_noise * 0.24
        body += bow * global_push * 0.30
        detail = smooth_detail * ui_detail_strength * (0.05 + 0.04 * gust)
        amp = ui_amplitude * edge_fade * (0.54 + 0.34 * gust)
        wave = (global_push * 0.22 + body + detail) * amp * wave_px_scale

        harmonic_offset = _harmonic_offset(point.s, x_norm, t_sec, ctx) * 0.55
        right_endpoint_weight = smoothstep(0.86, 1.0, point.s)
        endpoint_wave = math.sin(math.tau * (ui_speed * 0.95 * t_sec) + phase_endpoint)
        endpoint_offset = config.curve_arc_px * 0.12 * right_endpoint_weight * endpoint_wave
        curved.append(
            CenterPoint(
                x=point.x,
                y=point.y - arc_bias - wave - harmonic_offset - endpoint_offset,
                s=point.s,
            )
        )
    return curved


def _perturb_centerline(base_points: list[CenterPoint], ctx: SylContext, time_ms: int, frame_index: int) -> list[CenterPoint]:
    """Apply the curve profile plus static, flow, and boil perturbations to the centerline."""

    if len(base_points) < 2:
        return []

    config = ctx.config
    t_sec = time_ms / 1000.0
    phase_static = _phase_from_seed(ctx.seed, 1)
    phase_flow = _phase_from_seed(ctx.seed, 2)
    curved_points = _apply_curve_profile(base_points, ctx, time_ms)
    if len(curved_points) < 2:
        return []
    perturbed: list[CenterPoint] = []

    for i, point in enumerate(curved_points):
        prev_point = curved_points[i - 1] if i > 0 else curved_points[i + 1]
        next_point = curved_points[i + 1] if i < len(curved_points) - 1 else curved_points[i - 1]
        tx, ty = _normalize(next_point.x - prev_point.x, next_point.y - prev_point.y)
        nx, ny = (-ty, tx)
        static_term = config.static_amp_px * math.sin(math.tau * config.static_cycles * point.s + phase_static)
        flow_term = config.flow_amp_px * math.sin(
            math.tau * (config.flow_cycles * point.s + config.flow_speed_hz * t_sec) + phase_flow
        )
        boil_term = config.boil_amp_px * hash_noise(point.s * config.boil_cycles, frame_index, ctx.seed + 17)
        displacement = static_term + flow_term + boil_term
        perturbed.append(CenterPoint(x=point.x + nx * displacement, y=point.y + ny * displacement, s=point.s))
    return perturbed


def _clip_centerline(points: list[CenterPoint], x_cut: float) -> list[CenterPoint]:
    """Clip the centerline to keep only the visible right segment."""

    if len(points) < 2:
        return []

    visible: list[CenterPoint] = []
    for current, nxt in zip(points, points[1:]):
        current_in = current.x >= x_cut
        next_in = nxt.x >= x_cut
        if current_in:
            if not visible:
                visible.append(current)
            if next_in:
                visible.append(nxt)
                continue

            dx = nxt.x - current.x
            if abs(dx) <= FLOAT_EPSILON:
                break
            ratio = clamp((x_cut - current.x) / dx, 0.0, 1.0)
            y = lerp(current.y, nxt.y, ratio)
            s = lerp(current.s, nxt.s, ratio)
            visible.append(CenterPoint(x=x_cut, y=y, s=s))
            break

        if next_in:
            dx = nxt.x - current.x
            if abs(dx) <= FLOAT_EPSILON:
                continue
            ratio = clamp((x_cut - current.x) / dx, 0.0, 1.0)
            y = lerp(current.y, nxt.y, ratio)
            s = lerp(current.s, nxt.s, ratio)
            visible.append(CenterPoint(x=x_cut, y=y, s=s))
            visible.append(nxt)

    return visible if len(visible) >= 2 else []


def _clip_centerline_left(points: list[CenterPoint], x_cut: float) -> list[CenterPoint]:
    """Clip the centerline to keep only the visible left segment."""

    if len(points) < 2:
        return []

    visible: list[CenterPoint] = []
    for current, nxt in zip(points, points[1:]):
        current_in = current.x <= x_cut
        next_in = nxt.x <= x_cut
        if current_in:
            if not visible:
                visible.append(current)
            if next_in:
                visible.append(nxt)
                continue

            dx = nxt.x - current.x
            if abs(dx) <= FLOAT_EPSILON:
                break
            ratio = clamp((x_cut - current.x) / dx, 0.0, 1.0)
            y = lerp(current.y, nxt.y, ratio)
            s = lerp(current.s, nxt.s, ratio)
            visible.append(CenterPoint(x=x_cut, y=y, s=s))
            break

        if next_in:
            dx = nxt.x - current.x
            if abs(dx) <= FLOAT_EPSILON:
                continue
            ratio = clamp((x_cut - current.x) / dx, 0.0, 1.0)
            y = lerp(current.y, nxt.y, ratio)
            s = lerp(current.s, nxt.s, ratio)
            visible.append(CenterPoint(x=x_cut, y=y, s=s))
            visible.append(nxt)

    return visible if len(visible) >= 2 else []


def _polyline_length(points: Sequence[tuple[float, float]]) -> float:
    """Return the length of a polyline."""

    return sum(math.hypot(x2 - x1, y2 - y1) for (x1, y1), (x2, y2) in zip(points, points[1:]))


def _clip_polyline_prefix(points: list[tuple[float, float]], consume_px: float) -> list[tuple[float, float]]:
    """Remove a distance from the start of a polyline."""

    if len(points) < 2:
        return []
    remaining = max(0.0, consume_px)
    clipped: list[tuple[float, float]] = []
    for current, nxt in zip(points, points[1:]):
        seg_len = math.hypot(nxt[0] - current[0], nxt[1] - current[1])
        if seg_len <= FLOAT_EPSILON:
            continue
        if remaining >= seg_len:
            remaining -= seg_len
            continue
        if remaining > 0.0:
            t = remaining / seg_len
            start = (lerp(current[0], nxt[0], t), lerp(current[1], nxt[1], t))
            clipped.append(start)
            clipped.append(nxt)
            remaining = 0.0
        elif not clipped:
            clipped.append(current)
            clipped.append(nxt)
        else:
            clipped.append(nxt)
    return clipped if len(clipped) >= 2 else []


def _clip_polyline_to_length(points: list[tuple[float, float]], visible_px: float) -> list[tuple[float, float]]:
    """Keep only the first visible_px of a polyline."""

    if len(points) < 2 or visible_px <= 0.0:
        return []

    remaining = visible_px
    visible: list[tuple[float, float]] = [points[0]]
    for current, nxt in zip(points, points[1:]):
        seg_len = math.hypot(nxt[0] - current[0], nxt[1] - current[1])
        if seg_len <= FLOAT_EPSILON:
            continue
        if remaining >= seg_len:
            visible.append(nxt)
            remaining -= seg_len
            continue

        t = clamp(remaining / seg_len, 0.0, 1.0)
        visible.append((lerp(current[0], nxt[0], t), lerp(current[1], nxt[1], t)))
        break

    return visible if len(visible) >= 2 else []


def _build_right_knot_drawings(
    points: list[CenterPoint],
    ctx: SylContext,
    consume_px: float = 0.0,
    visible_px: float | None = None,
) -> list[str]:
    """Build a natural loop knot from separately layered stroke pieces."""

    if not ctx.config.tail_knot_enabled or len(points) < 2:
        return []

    right_point = points[-1]
    prev_point = points[-2]
    tx, ty = _normalize(right_point.x - prev_point.x, right_point.y - prev_point.y)
    nx, ny = (-ty, tx)

    if len(points) >= 3:
        prev2_point = points[-3]
        v1x = prev_point.x - prev2_point.x
        v1y = prev_point.y - prev2_point.y
        v2x = right_point.x - prev_point.x
        v2y = right_point.y - prev_point.y
        curvature_cross = v1x * v2y - v1y * v2x
    else:
        curvature_cross = 0.0

    curve_sign = -1.0 if ((ctx.seed // 17) % 2 == 0) else 1.0
    bend_amount = clamp(curvature_cross / max(1.0, ctx.config.tail_knot_radius_px * 3.0), -1.0, 1.0)
    bend_smooth = math.sin(bend_amount * math.pi * 0.5)

    radius_jitter = hash_noise(0.341, 251, ctx.seed)
    radius = max(2.0, ctx.config.tail_knot_radius_px * (0.96 + 0.06 * radius_jitter))
    lift = radius * 0.18 * bend_smooth
    canvas_radius = 25.0
    scale = radius / canvas_radius

    def local_to_world(x: float, y: float) -> tuple[float, float]:
        local_x = x * scale
        local_y = y * scale + lift * smoothstep(0.0, 1.0, x / 50.0)
        return (
            right_point.x + tx * local_x + nx * local_y * curve_sign,
            right_point.y + ty * local_x + ny * local_y * curve_sign,
        )

    def polyline_world_drawing(world_xy: list[tuple[float, float]]) -> str:
        world_points = [
            CenterPoint(x=x, y=y, s=index / max(1, len(world_xy) - 1))
            for index, (x, y) in enumerate(world_xy)
        ]
        ribbon = _build_ribbon_path(world_points, ctx)
        return _ribbon_to_drawing(ribbon) if ribbon is not None else ""

    def local_polyline_to_world(local_points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        return [local_to_world(x, y) for x, y in local_points]

    def continuation_world_points() -> list[tuple[float, float]]:
        extend_px = max(0.0, ctx.config.tail_knot_extend_px)
        if extend_px <= 0.0:
            return []

        recent = points[-min(8, len(points)) :]
        numerator = 0.0
        denominator = 0.0
        for point in recent[:-1]:
            dx = point.x - right_point.x
            dy = point.y - right_point.y
            local_x = dx * tx + dy * ty
            local_y = (dx * nx + dy * ny) * curve_sign
            if local_x >= -1e-3:
                continue
            x2 = local_x * local_x
            numerator += local_y * x2
            denominator += x2 * x2

        curvature = numerator / denominator if denominator > FLOAT_EPSILON else 0.0
        max_wave_px = max(1.0, ctx.config.curve_wave_amp_px * 0.45)
        knot_exit_x, knot_exit_y = local_to_world(50.0, 0.0)
        return [
            (
                knot_exit_x + tx * distance + nx * curve_sign * clamp(curvature * distance * distance, -max_wave_px, max_wave_px),
                knot_exit_y + ty * distance + ny * curve_sign * clamp(curvature * distance * distance, -max_wave_px, max_wave_px),
            )
            for distance in (extend_px * i / 24 for i in range(25))
        ]

    def take_ordered_paths(paths: list[list[tuple[float, float]]]) -> list[str]:
        drawings: list[str] = []
        if visible_px is not None:
            remaining_visible = max(0.0, visible_px)
            for path in paths:
                if remaining_visible <= 0.0:
                    break
                path_len = _polyline_length(path)
                if path_len <= FLOAT_EPSILON:
                    continue
                visible = _clip_polyline_to_length(path, remaining_visible)
                remaining_visible -= path_len
                drawing = polyline_world_drawing(visible)
                if drawing:
                    drawings.append(drawing)
            return drawings

        seam_trim_px = max(0.75, ctx.config.line_width_px * 0.35) if consume_px > 0.0 else 0.0
        remaining = max(0.0, consume_px + seam_trim_px)
        for path in paths:
            path_len = _polyline_length(path)
            if remaining >= path_len:
                remaining -= path_len
                continue
            visible = _clip_polyline_prefix(path, remaining)
            remaining = 0.0
            drawing = polyline_world_drawing(visible)
            if drawing:
                drawings.append(drawing)
        return drawings

    circle_cx = 25.0
    circle_cy = 25.0
    circle_r = 25.0
    left_x = 25.0 - 25.0 / math.sqrt(2.0)
    right_x = 25.0 + 25.0 / math.sqrt(2.0)
    cut_y = 25.0 - 25.0 / math.sqrt(2.0)
    cubic_start_slope = 0.0
    cubic_end_slope = 1.0

    def solve_cubic_coefficients(
        x0: float,
        y0: float,
        slope0: float,
        x1: float,
        y1: float,
        slope1: float,
    ) -> tuple[float, float, float, float]:
        dx = x1 - x0
        if abs(dx) <= FLOAT_EPSILON:
            return (0.0, 0.0, slope0, y0)
        dy = y1 - y0
        a_local = ((slope1 + slope0) * dx - 2.0 * dy) / (dx**3)
        b_local = (3.0 * dy - (slope1 + 2.0 * slope0) * dx) / (dx**2)
        a = a_local
        b = b_local - 3.0 * a_local * x0
        c = slope0 + 3.0 * a_local * x0 * x0 - 2.0 * b_local * x0
        d = y0 - a * x0**3 - b * x0**2 - c * x0
        return (a, b, c, d)

    cubic_a, cubic_b, cubic_c, cubic_d = solve_cubic_coefficients(
        0.0,
        0.0,
        cubic_start_slope,
        right_x,
        cut_y,
        cubic_end_slope,
    )

    def cubic_y(x: float) -> float:
        base_y = cubic_a * x**3 + cubic_b * x**2 + cubic_c * x + cubic_d
        u = clamp(x / max(FLOAT_EPSILON, right_x), 0.0, 1.0)
        mid_lift = math.sin(math.pi * u) ** 2
        return base_y + circle_r * 0.13 * mid_lift

    arc_points: list[tuple[float, float]] = []
    arc_samples = 120
    arc_start = math.radians(315.0)
    arc_end = math.radians(585.0)
    for index in range(arc_samples):
        theta = lerp(arc_start, arc_end, index / (arc_samples - 1))
        arc_points.append((circle_cx + circle_r * math.cos(theta), circle_cy + circle_r * math.sin(theta)))
    upper_points = [
        (right_x * i / 72, cubic_y(right_x * i / 72))
        for i in range(73)
    ]
    lower_gap_start = 0.62
    lower_gap_end = 0.76
    lower_full = [
        (
            50.0 - right_x * i / 72,
            cubic_y(right_x * i / 72),
        )
        for i in range(72, -1, -1)
    ]
    lower_left_points = lower_full[: round(lower_gap_start * (len(lower_full) - 1)) + 1]
    lower_right_points = lower_full[round(max(lower_gap_start, lower_gap_end - 0.025) * (len(lower_full) - 1)) :]
    ordered_paths = [
        local_polyline_to_world(upper_points),
        local_polyline_to_world(arc_points),
        local_polyline_to_world(lower_left_points),
        local_polyline_to_world(lower_right_points),
        continuation_world_points(),
    ]
    return take_ordered_paths(ordered_paths)


def _scaled_syl_context(ctx: SylContext, *, growth: float) -> SylContext:
    """Scale early lead-in geometry while preserving the main waveform family."""

    config = ctx.config
    scaled_config = replace(
        config,
        line_width_px=max(0.8, config.line_width_px * lerp(0.38, 1.0, growth)),
        curve_arc_px=max(2.0, config.curve_arc_px * lerp(0.22, 1.0, growth)),
        curve_wave_amp_px=max(1.0, config.curve_wave_amp_px * lerp(0.24, 1.0, growth)),
        static_amp_px=config.static_amp_px * lerp(0.15, 1.0, growth),
        flow_amp_px=config.flow_amp_px * lerp(0.2, 1.0, growth),
        boil_amp_px=config.boil_amp_px * lerp(0.12, 1.0, growth),
        tail_knot_radius_px=max(3.0, config.tail_knot_radius_px * lerp(0.35, 1.0, growth)),
        tail_knot_extend_px=config.tail_knot_extend_px * lerp(0.2, 1.0, growth),
    )
    return SylContext(line=ctx.line, syl=ctx.syl, seed=ctx.seed, config=scaled_config)


def _build_ribbon_path(points: list[CenterPoint], ctx: SylContext) -> RibbonPath | None:
    """Expand a centerline into a ribbon path."""

    if len(points) < 2:
        return None

    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    config = ctx.config
    for i, point in enumerate(points):
        prev_point = points[i - 1] if i > 0 else points[i + 1]
        next_point = points[i + 1] if i < len(points) - 1 else points[i - 1]
        tx, ty = _normalize(next_point.x - prev_point.x, next_point.y - prev_point.y)
        nx, ny = (-ty, tx)
        width_noise = hash_noise(point.s, 0, ctx.seed + 1)
        half_width = 0.5 * config.line_width_px * (1.0 + config.line_width_jitter_ratio * width_noise)
        left.append((point.x + nx * half_width, point.y + ny * half_width))
        right.append((point.x - nx * half_width, point.y - ny * half_width))

    if len(left) != len(right) or len(left) < 2:
        return None
    return RibbonPath(left=left, right=right)


def _endpoint_cap_arc(path: RibbonPath, *, at_start: bool) -> list[tuple[float, float]]:
    """Approximate a round cap on either ribbon endpoint."""

    if len(path.left) < 2 or len(path.right) < 2:
        return []

    if at_start:
        center = ((path.left[0][0] + path.right[0][0]) * 0.5, (path.left[0][1] + path.right[0][1]) * 0.5)
        tangent = _normalize(path.left[1][0] - path.left[0][0], path.left[1][1] - path.left[0][1])
        edge_point = path.left[0]
        start_angle_offset = math.pi / 2
        angle_step = math.pi / 5
    else:
        center = ((path.left[-1][0] + path.right[-1][0]) * 0.5, (path.left[-1][1] + path.right[-1][1]) * 0.5)
        tangent = _normalize(path.left[-1][0] - path.left[-2][0], path.left[-1][1] - path.left[-2][1])
        edge_point = path.right[-1]
        start_angle_offset = -math.pi / 2
        angle_step = math.pi / 5

    radius = math.hypot(edge_point[0] - center[0], edge_point[1] - center[1])
    if radius <= FLOAT_EPSILON:
        return []

    theta = math.atan2(tangent[1], tangent[0])
    return [
        (
            center[0] + radius * math.cos(theta + start_angle_offset + angle_step * i),
            center[1] + radius * math.sin(theta + start_angle_offset + angle_step * i),
        )
        for i in range(6)
    ]


def _ribbon_to_drawing(path: RibbonPath) -> str:
    """Convert a ribbon path into a filled drawing."""

    start_cap = _endpoint_cap_arc(path, at_start=True)
    end_cap = _endpoint_cap_arc(path, at_start=False)
    if not start_cap or not end_cap:
        polygon = path.left + list(reversed(path.right))
        return _format_drawing(polygon)

    polygon = start_cap + path.right[1:-1] + end_cap + list(reversed(path.left[1:-1]))
    return _format_drawing(polygon)


def _segment_ribbon_drawing(x1: float, y1: float, x2: float, y2: float, width: float) -> str:
    """Build a small ribbon polygon for a spark slash."""

    tx, ty = _normalize(x2 - x1, y2 - y1)
    nx, ny = (-ty, tx)
    hw = width * 0.5
    arc_degrees = 120.0
    arc_segments = 4
    arc_radius = hw / math.sin(math.radians(arc_degrees * 0.5))
    center_offset = arc_radius * math.cos(math.radians(arc_degrees * 0.5))

    start_left = (x1 + nx * hw, y1 + ny * hw)
    start_right = (x1 - nx * hw, y1 - ny * hw)
    end_left = (x2 + nx * hw, y2 + ny * hw)
    end_right = (x2 - nx * hw, y2 - ny * hw)

    start_center = (x1 + tx * center_offset, y1 + ty * center_offset)
    end_center = (x2 - tx * center_offset, y2 - ty * center_offset)

    def build_arc(
        center: tuple[float, float],
        start_point: tuple[float, float],
        end_point: tuple[float, float],
    ) -> list[tuple[float, float]]:
        start_angle = math.atan2(start_point[1] - center[1], start_point[0] - center[0])
        end_angle = math.atan2(end_point[1] - center[1], end_point[0] - center[0])
        if end_angle <= start_angle:
            end_angle += math.tau
        return [
            (
                center[0] + arc_radius * math.cos(lerp(start_angle, end_angle, i / arc_segments)),
                center[1] + arc_radius * math.sin(lerp(start_angle, end_angle, i / arc_segments)),
            )
            for i in range(arc_segments + 1)
        ]

    start_cap = build_arc(start_center, start_left, start_right)
    end_cap = build_arc(end_center, end_right, end_left)
    polygon = start_cap + [end_right] + end_cap[1:] + [start_left]
    return _format_drawing(polygon)


def _round_ms(value: float) -> float:
    """Round millisecond values to 3 decimal places for stable fractional timing."""

    return round(float(value), 3)


def _ass_timestamp_from_ms(value_ms: float) -> str:
    """Convert fractional milliseconds into a standard ASS timestamp."""

    value_ms = max(0.0, _round_ms(value_ms))
    total_centiseconds = int(math.floor((value_ms + 5.0) / 10.0))
    hours = (total_centiseconds // 360000) % 10
    minutes = (total_centiseconds // 6000) % 60
    seconds = (total_centiseconds // 100) % 60
    centiseconds = total_centiseconds % 100
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def _cached_ass_timestamp(time_cache: dict[float, str], value_ms: float) -> str:
    """Return a cached ASS timestamp for a rounded millisecond value."""

    rounded_ms = max(0.0, _round_ms(value_ms))
    timestamp = time_cache.get(rounded_ms)
    if timestamp is None:
        timestamp = _ass_timestamp_from_ms(rounded_ms)
        time_cache[rounded_ms] = timestamp
    return timestamp


def _format_ass_event(
    *,
    event_kind: str,
    layer: int,
    start_text: str,
    end_text: str,
    style: str,
    actor: str,
    margin_l: str,
    margin_r: str,
    margin_v: str,
    effect: str,
    text: str,
) -> str:
    """Serialize one ASS event line."""

    return (
        f"{event_kind}: {layer},{start_text},{end_text},{style},{actor},"
        f"{margin_l},{margin_r},{margin_v},{effect},{text}\n"
    )


def _slice_step_ms(config: MeltConfig) -> float:
    """Compute the effective slice duration."""

    fps_step = _round_ms(1000.0 / max(0.001, config.anim_fps))
    return max(float(config.min_slice_ms), fps_step)


def _compute_mask_fronts(ctx: SylContext, slice_count: int) -> list[float]:
    """Compute a strictly increasing left-to-right mask front sequence."""

    base_points = _sample_centerline(ctx)
    if len(base_points) < 2 or slice_count <= 0:
        return []

    x_left = base_points[0].x
    x_right = base_points[-1].x
    span = max(1.0, x_right - x_left)
    fronts: list[float] = []
    previous = x_left - 1.0
    for frame_index in range(slice_count):
        u = (frame_index + 1) / slice_count
        jitter = ctx.config.mask_edge_jitter_px * 0.35 * hash_noise(u, frame_index, ctx.seed + 333)
        candidate = x_left + span * u + jitter
        candidate = clamp(candidate, x_left, x_right)
        if candidate <= previous:
            candidate = previous + max(0.5, span / (slice_count * 8.0))
        candidate = min(x_right, candidate)
        previous = candidate
        fronts.append(candidate)
    return fronts


def _estimate_right_knot_length_px(ctx: SylContext) -> float:
    """Estimate the path length used by the right-side knot wipe."""

    if not ctx.config.tail_knot_enabled:
        return 0.0
    canvas_radius = 25.0
    scale = max(0.01, ctx.config.tail_knot_radius_px / canvas_radius)
    upper_len = math.hypot(25.0 + 25.0 / math.sqrt(2.0), 25.0 - 25.0 / math.sqrt(2.0))
    arc_len = math.radians(585.0 - 315.0) * 25.0
    lower_len = math.hypot(50.0 - (25.0 - 25.0 / math.sqrt(2.0)), 25.0 - 25.0 / math.sqrt(2.0))
    return (upper_len + arc_len + lower_len) * scale + max(0.0, ctx.config.tail_knot_extend_px)


def _smooth_follow_anchor(
    current_x: float,
    current_y: float,
    target_x: float,
    target_y: float,
    step_ms: float,
    config: MeltConfig,
) -> tuple[float, float]:
    """Lag a follower toward a moving point with frame-rate stable smoothing."""

    frame_ms = 1000.0 / max(0.001, config.anim_fps)
    step_ratio = max(0.25, step_ms / frame_ms)
    retain_x = clamp(config.spark_follow_smooth_x, 0.0, 0.98) ** step_ratio
    retain_y = clamp(config.spark_follow_smooth_y, 0.0, 0.98) ** step_ratio
    return (
        lerp(current_x, target_x, 1.0 - retain_x),
        lerp(current_y, target_y, 1.0 - retain_y),
    )


def _build_spark_cluster(ctx: SylContext, x: float, y: float) -> list[SparkSlash]:
    """Create static spark slash definitions."""

    rng = random.Random(ctx.seed + 701)
    count = rng.randint(ctx.config.spark_count_min, ctx.config.spark_count_max)
    spread_min = ctx.config.spark_spread_deg_min
    spread_max = ctx.config.spark_spread_deg_max
    spread_span = spread_max - spread_min
    base_jitter = ctx.config.spark_jitter_amp_px
    base_length = 0.5 * (ctx.config.spark_len_min_px + ctx.config.spark_len_max_px)
    sparks: list[SparkSlash] = []
    for index in range(count):
        spark_seed = ctx.seed + 10_000 + index
        lane = 0.5 if count == 1 else index / (count - 1)
        lane_centered = lane - 0.5
        lane_bias = lane_centered * 20.0
        base_angle = spread_min + spread_span * lane + lane_bias
        anchor_offset_x = lerp(-base_jitter * 1.6, base_jitter * 1.6, lane) + rng.uniform(-base_jitter * 0.4, base_jitter * 0.4)
        anchor_offset_y = lerp(-base_jitter * 1.1, base_jitter * 1.1, lane) + rng.uniform(-base_jitter * 0.35, base_jitter * 0.35)
        sparks.append(
            SparkSlash(
                seed=spark_seed,
                anchor_x=x,
                anchor_y=y,
                angle_deg=clamp(base_angle + rng.uniform(-6.0, 6.0), spread_min - 6.0, spread_max + 6.0),
                length=base_length,
                anchor_offset_x=anchor_offset_x,
                anchor_offset_y=anchor_offset_y,
                angle_wobble_deg=rng.uniform(4.0, 10.0),
                length_wobble_px=base_length * rng.uniform(0.06, 0.14),
                phase_x=_phase_from_seed(spark_seed, 81),
                phase_y=_phase_from_seed(spark_seed, 82),
                phase_angle=_phase_from_seed(spark_seed, 83),
                phase_length=_phase_from_seed(spark_seed, 84),
            )
        )
    return sparks


def _spark_drawing(ctx: SylContext, spark: SparkSlash, anchor_x: float, anchor_y: float, u: float, frame_index: int) -> str:
    """Build one spark slash drawing for a frame."""

    t = frame_index * 0.42
    step_jitter_x = ctx.config.spark_jitter_amp_px * 0.32 * hash_noise(u * 1.7 + frame_index * 0.11, 91, spark.seed)
    step_jitter_y = ctx.config.spark_jitter_amp_px * 0.28 * hash_noise(u * 1.9 + frame_index * 0.09, 92, spark.seed)
    jitter_x = ctx.config.spark_jitter_amp_px * 0.70 * math.sin(t + spark.phase_x) + step_jitter_x
    jitter_y = ctx.config.spark_jitter_amp_px * 0.60 * math.sin(t * 0.88 + spark.phase_y) + step_jitter_y
    drift_x = -ctx.config.spark_drift_px * u
    step_angle = spark.angle_wobble_deg * 0.64 * hash_noise(u * 1.5 + frame_index * 0.13, 93, spark.seed)
    angle_wobble = spark.angle_wobble_deg * math.sin(t * 0.74 + spark.phase_angle) + step_angle
    angle_deg = clamp(
        spark.angle_deg + angle_wobble,
        ctx.config.spark_spread_deg_min - 8.0,
        ctx.config.spark_spread_deg_max + 8.0,
    )
    angle = math.radians(angle_deg)
    step_length = spark.length_wobble_px * 0.42 * hash_noise(u * 1.3 + frame_index * 0.15, 94, spark.seed)
    length = clamp(
        spark.length + spark.length_wobble_px * math.sin(t * 0.68 + spark.phase_length) + step_length,
        ctx.config.spark_len_min_px,
        ctx.config.spark_len_max_px,
    )
    spread_push = 0.48 * length
    origin_x = anchor_x + spark.anchor_offset_x + jitter_x + drift_x + math.cos(angle) * spread_push
    origin_y = anchor_y + spark.anchor_offset_y + jitter_y + math.sin(angle) * spread_push
    x2 = origin_x + math.cos(angle) * length
    y2 = origin_y + math.sin(angle) * length
    return _segment_ribbon_drawing(origin_x, origin_y, x2, y2, ctx.config.line_width_px)


def _body_drawing(scale: float) -> str:
    """Build the butterfly body drawing."""

    points = [
        (0.0, -7.0 * scale),
        (3.0 * scale, -1.0 * scale),
        (2.0 * scale, 7.0 * scale),
        (-2.0 * scale, 7.0 * scale),
        (-3.0 * scale, -1.0 * scale),
    ]
    return _format_drawing(points)


def _wing_frame(scale: float, span: float, lift: float, frame_index: int) -> ButterflyFrame:
    """Build one butterfly wing frame."""

    left = [
        (-2.0 * scale, 0.0),
        (-8.0 * span * scale, -6.0 * lift * scale),
        (-15.0 * span * scale, -3.0 * lift * scale),
        (-11.0 * span * scale, 0.0),
        (-15.0 * span * scale, 4.0 * lift * scale),
        (-8.0 * span * scale, 6.0 * lift * scale),
        (-2.0 * scale, 1.0 * scale),
    ]
    right = [(-x, y) for x, y in reversed(left)]
    drawing = _format_drawing(left + right)
    return ButterflyFrame(frame_index=frame_index, drawing=drawing, bounds=_drawing_bounds(drawing))


def _butterfly_frames(scale: float) -> list[ButterflyFrame]:
    """Load the external butterfly animation frames."""

    global _BUTTERFLY_FRAME_CACHE
    if _BUTTERFLY_FRAME_CACHE is None:
        source_path = os.path.join(os.path.dirname(__file__), BUTTERFLY_FRAME_SOURCE)
        frames: list[ButterflyFrame] = []
        with open(source_path, encoding="utf-8-sig") as source_file:
            for line in source_file:
                if not line.startswith("Dialogue:"):
                    continue
                match = BUTTERFLY_DRAWING_RE.search(line)
                if match is None:
                    continue
                frames.append(
                    ButterflyFrame(
                        frame_index=len(frames),
                        drawing=match.group("drawing").strip(),
                        scale=int(match.group("scale")),
                        bounds=_drawing_bounds(match.group("drawing").strip()),
                    )
                )

        if len(frames) != 10:
            raise ValueError(f"expected 10 butterfly frames in {source_path!r}, found {len(frames)}")
        _BUTTERFLY_FRAME_CACHE = tuple(frames)

    return list(_BUTTERFLY_FRAME_CACHE)


def _drawing_scale_divisor(scale: int) -> int:
    """Return the ASS drawing coordinate divisor for a \\p scale."""

    return 2 ** max(0, scale - 1)


def _random_signed_magnitude(rng: random.Random, minimum: float, maximum: float) -> float:
    """Return a random signed value whose magnitude stays away from zero."""

    magnitude = rng.uniform(minimum, max(minimum, maximum))
    return magnitude if rng.random() < 0.5 else -magnitude


def _alpha_lerp(base_alpha: str, t: float) -> str:
    """Interpolate an ASS alpha value toward fully transparent."""

    base = int(base_alpha[2:4], 16)
    value = round(lerp(base, 255.0, clamp(t, 0.0, 1.0)))
    return f"&H{value:02X}&"


def _shift_syl_geometry(syl: object, y_offset: float) -> None:
    """Apply collision offset to common syllable coordinates."""

    for attr in ("top", "bottom", "middle"):
        if hasattr(syl, attr):
            setattr(syl, attr, getattr(syl, attr) + y_offset)


def _syl_seed(config: MeltConfig, line: Line, syl: object) -> int:
    """Return the deterministic syllable seed."""

    return config.random_seed + line.i * 1000 + syl.i


def _should_emit_butterfly(
    *,
    gap: int,
    config: MeltConfig,
    rng: random.Random,
) -> bool:
    """Decide whether to emit a butterfly using a capped pity curve."""

    min_gap = max(1, config.butterfly_min_syllable_gap)
    max_gap = max(min_gap, config.butterfly_max_syllable_gap)
    if gap < min_gap:
        return False
    if gap >= max_gap:
        return True

    probability = clamp((gap - min_gap + 1) * config.butterfly_gap_probability_step, 0.0, 1.0)
    return rng.random() < probability


def _select_target_lines(
    lines: list[Line],
    selector: Callable[[Line], bool] | None = None,
) -> list[tuple[int, Line]]:
    """Select renderable target lines."""

    selector = selector or (lambda line: not line.comment and line.styleref.alignment in (1, 2, 3))
    result: list[tuple[int, Line]] = []
    line_index = 0
    for line in lines:
        if not selector(line):
            continue
        result.append((line_index, line))
        line_index += 1
    return result


def text_to_layer_shapes(syl, config: MeltConfig) -> list[LayerShape]:
    """Build the material layers used by the main stroke."""

    return [
        LayerShape(drawing="", color=config.line_color_main, alpha=config.line_alpha_main, layer_offset=0, blur=0.0),
        LayerShape(drawing="", color=config.line_color_soft, alpha=config.line_alpha_soft, layer_offset=1, blur=config.soft_blur),
    ]


def _build_syl_context(line: Line, syl: object, config: MeltConfig) -> SylContext:
    """Create the shared per-syllable render context."""

    return SylContext(line=line, syl=syl, seed=_syl_seed(config, line, syl), config=config)


def _append_layered_drawings(
    events: list[OutputEvent],
    *,
    drawing: str,
    extra_drawings: Sequence[str],
    layers: Sequence[LayerShape],
    line_layer_base: int,
    style_name: str,
    start_time: float,
    end_time: float,
    extra_tags_builder: Callable[[LayerShape], str] | None = None,
) -> None:
    """Append stroke drawings for every material layer."""

    drawings = ([drawing] if drawing else []) + list(extra_drawings)
    if not drawings:
        return

    for layer in layers:
        for layer_drawing in drawings:
            events.append(
                OutputEvent(
                    layer=line_layer_base + layer.layer_offset,
                    style=style_name,
                    start_time=start_time,
                    end_time=end_time,
                    text=_drawing_to_text(
                        layer_drawing,
                        color=layer.color,
                        alpha=layer.alpha,
                        blur=layer.blur,
                        extra_tags="" if extra_tags_builder is None else extra_tags_builder(layer),
                    ),
                )
            )


def _build_static_stroke_events(
    *,
    ctx: SylContext,
    layers: Sequence[LayerShape],
    line_layer_base: int,
    style_name: str,
    start_time: float,
    end_time: float,
    frame_index: int = 0,
) -> list[OutputEvent]:
    """Build one full, unsliced stroke frame."""

    base_points = _sample_centerline(ctx)
    if len(base_points) < 2:
        return []

    static_points = _perturb_centerline(base_points, ctx, start_time, frame_index)
    ribbon = _build_ribbon_path(static_points, ctx)
    if ribbon is None:
        return []

    drawing = _ribbon_to_drawing(ribbon)
    if not drawing:
        return []

    events: list[OutputEvent] = []
    _append_layered_drawings(
        events,
        drawing=drawing,
        extra_drawings=_build_right_knot_drawings(static_points, ctx),
        layers=layers,
        line_layer_base=line_layer_base,
        style_name=style_name,
        start_time=start_time,
        end_time=end_time,
    )
    return events


def _build_full_shape_events(
    *,
    line,
    syl,
    layers: list[LayerShape],
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
) -> list[OutputEvent]:
    """Build a rapid left-to-right lead-in for the main stroke."""

    ctx = _build_syl_context(line, syl, config)
    syl_start_abs = _syl_time_to_absolute(line, syl.start_time)
    lead_start = _round_ms(max(0.0, syl_start_abs - config.line_lead_in_ms))
    lead_end = _round_ms(syl_start_abs)
    if lead_end <= lead_start:
        return []

    base_points = _sample_centerline(ctx)
    if len(base_points) < 2:
        return []

    main_left = base_points[0].x
    main_right = base_points[-1].x
    main_span = max(1.0, main_right - main_left)
    knot_span = _estimate_right_knot_length_px(ctx)
    total_span = main_span + knot_span
    step_ms = _slice_step_ms(config)
    slice_count = min(MAX_RENDER_SLICES, max(1, math.ceil(max(1.0, lead_end - lead_start) / step_ms)))
    events: list[OutputEvent] = []

    for frame_index in range(slice_count):
        start_time = _round_ms(lead_start + frame_index * step_ms)
        end_time = lead_end if frame_index == slice_count - 1 else _round_ms(min(lead_end, start_time + step_ms))
        if end_time <= start_time:
            continue

        u = (frame_index + 1) / slice_count
        growth = smoothstep(0.0, 1.0, u)
        scaled_ctx = _scaled_syl_context(ctx, growth=growth)
        scaled_base_points = _sample_centerline(scaled_ctx)
        if len(scaled_base_points) < 2:
            continue
        perturbed = _perturb_centerline(scaled_base_points, scaled_ctx, start_time, frame_index)
        progressed = total_span * growth
        main_visible = min(main_span, progressed)
        if main_visible <= FLOAT_EPSILON:
            continue
        front_x = main_left + main_visible
        clipped = _clip_centerline_left(perturbed, front_x)
        ribbon = _build_ribbon_path(clipped, scaled_ctx) if len(clipped) >= 2 else None
        drawing = _ribbon_to_drawing(ribbon) if ribbon is not None else ""

        knot_visible = max(0.0, progressed - main_span)
        knot_drawings = _build_right_knot_drawings(perturbed, scaled_ctx, visible_px=knot_visible)
        if not drawing and not knot_drawings:
            continue

        _append_layered_drawings(
            events,
            drawing=drawing,
            extra_drawings=knot_drawings,
            layers=layers,
            line_layer_base=line_layer_base,
            style_name=style_name,
            start_time=start_time,
            end_time=end_time,
        )

    return events


def _build_vector_mask_events(
    *,
    line,
    syl,
    layers: list[LayerShape],
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
) -> list[OutputEvent]:
    """Build the sliced main stroke and spark-cluster events."""

    if syl.right - syl.left < 2:
        return []

    ctx = _build_syl_context(line, syl, config)
    base_points = _sample_centerline(ctx)
    if len(base_points) < 2:
        return []

    syl_start_abs = _syl_time_to_absolute(line, syl.start_time)
    syl_end_abs = _syl_time_to_absolute(line, syl.end_time)
    dt = max(1.0, _round_ms(syl.end_time - syl.start_time))
    if dt < config.min_slice_ms:
        return _build_static_stroke_events(
            ctx=ctx,
            layers=layers,
            line_layer_base=line_layer_base,
            style_name=style_name,
            start_time=_round_ms(syl_start_abs),
            end_time=_round_ms(syl_end_abs),
        )

    step_ms = _slice_step_ms(config)
    slice_count = min(MAX_RENDER_SLICES, max(1, math.ceil(dt / step_ms)))
    main_left = base_points[0].x
    main_right = base_points[-1].x
    main_span = max(1.0, main_right - main_left)
    knot_span = _estimate_right_knot_length_px(ctx)
    knot_wipe_speed = max(0.1, config.tail_knot_wipe_speed)
    wipe_span = main_span + knot_span / knot_wipe_speed

    sparks = _build_spark_cluster(ctx, base_points[0].x, base_points[0].y)
    follow_x = base_points[0].x
    follow_y = base_points[0].y
    events = _build_full_shape_events(
        line=line,
        syl=syl,
        layers=layers,
        line_layer_base=line_layer_base,
        style_name=style_name,
        config=config,
    )
    main_seam_trim_px = max(0.75, config.line_width_px * 0.35)
    spark_prefade_start = syl_end_abs - config.spark_prefade_lead_ms
    spark_prefade_ms = max(1.0, config.spark_prefade_lead_ms)
    spark_tail_fade_ms = max(1.0, config.spark_tail_fade_ms)

    for frame_index in range(slice_count):
        start_time = _round_ms(syl_start_abs + frame_index * step_ms)
        end_time = (
            _round_ms(syl_end_abs)
            if frame_index == slice_count - 1
            else _round_ms(min(syl_end_abs, start_time + step_ms))
        )
        if end_time <= start_time:
            continue

        time_ms = start_time
        perturbed = _perturb_centerline(base_points, ctx, time_ms, frame_index)
        wipe_u = 0.0 if slice_count <= 1 else frame_index / (slice_count - 1)
        wipe_progress = wipe_span * wipe_u
        main_cut = main_left + min(main_span, wipe_progress)
        knot_consume = max(0.0, wipe_progress - main_span) * knot_wipe_speed

        clipped = _clip_centerline(perturbed, main_cut) if main_cut < main_right - main_seam_trim_px else []
        ribbon = _build_ribbon_path(clipped, ctx) if len(clipped) >= 2 else None
        drawing = _ribbon_to_drawing(ribbon) if ribbon is not None else ""
        knot_drawings = _build_right_knot_drawings(perturbed, ctx, knot_consume)

        if ribbon is not None:
            left_tip_x = (ribbon.left[0][0] + ribbon.right[0][0]) * 0.5
            left_tip_y = (ribbon.left[0][1] + ribbon.right[0][1]) * 0.5
            follow_x, follow_y = _smooth_follow_anchor(follow_x, follow_y, left_tip_x, left_tip_y, step_ms, config)
        u = clamp((time_ms - syl_start_abs) / dt, 0.0, 1.0)

        if not drawing and not knot_drawings:
            continue

        _append_layered_drawings(
            events,
            drawing=drawing,
            extra_drawings=knot_drawings,
            layers=layers,
            line_layer_base=line_layer_base,
            style_name=style_name,
            start_time=start_time,
            end_time=end_time,
        )

        for spark in sparks:
            time_fade_progress = clamp(
                (start_time - spark_prefade_start) / spark_prefade_ms,
                0.0,
                1.0,
            )
            knot_fade_progress = clamp(knot_consume / spark_tail_fade_ms, 0.0, 1.0)
            intro_fade_progress = 1.0 - clamp(
                (start_time - syl_start_abs) / max(1.0, config.line_fade_in_ms),
                0.0,
                1.0,
            )
            spark_alpha = _alpha_lerp(
                config.spark_alpha,
                max(time_fade_progress, knot_fade_progress, intro_fade_progress),
            )
            if spark_alpha == "&HFF&":
                continue
            events.append(
                OutputEvent(
                    layer=line_layer_base + 2,
                    style=style_name,
                    start_time=start_time,
                    end_time=end_time,
                    text=_drawing_to_text(
                        _spark_drawing(ctx, spark, follow_x, follow_y, u, frame_index),
                        color=config.spark_color,
                        alpha=spark_alpha,
                        blur=0.0,
                    ),
                )
            )

    return events


def _build_spike_events(
    *,
    line,
    syl,
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
) -> list[OutputEvent]:
    """Build butterfly launch events for the syllable tail."""

    tb = max(1, config.butterfly_duration_ms)
    tf = min(config.butterfly_fade_ms, tb)
    step_ms = _slice_step_ms(config)
    slice_count = min(MAX_RENDER_SLICES, max(1, math.ceil(tb / step_ms)))
    frames = _butterfly_frames(config.butterfly_scale)
    seed = _syl_seed(config, line, syl)
    frame_rng = _noise_seed(seed, 503)
    motion_rng = _noise_seed(seed, 509)
    start_frame = frame_rng.randrange(len(frames))
    drawing_scale = max(0.01, config.butterfly_scale)
    fsc = (
        ""
        if math.isclose(drawing_scale, 1.0, abs_tol=FLOAT_EPSILON)
        else f"\\fscx{drawing_scale * 100:g}\\fscy{drawing_scale * 100:g}"
    )
    syl_end_abs = _syl_time_to_absolute(line, syl.end_time)
    fade_start = syl_end_abs + tb - tf
    x0 = (syl.left + syl.right) * 0.5 + motion_rng.uniform(
        -config.butterfly_spawn_jitter_x_px, config.butterfly_spawn_jitter_x_px
    )
    y0 = getattr(syl, "middle", (syl.top + syl.bottom) * 0.5) - motion_rng.uniform(
        0.0, config.butterfly_spawn_jitter_up_px
    )
    base_distance = math.hypot(config.butterfly_dx_px, config.butterfly_dy_px)
    direction_angle = math.radians(
        motion_rng.uniform(config.butterfly_direction_min_deg, config.butterfly_direction_max_deg)
    )
    move_dx = math.cos(direction_angle) * base_distance
    move_dy = math.sin(direction_angle) * base_distance
    arc_side = -1.0 if motion_rng.random() < 0.5 else 1.0
    arc_x = -math.sin(direction_angle) * config.butterfly_arc_px * arc_side
    arc_y = math.cos(direction_angle) * config.butterfly_arc_px * arc_side
    base_rotation_angle = -(math.degrees(direction_angle) + config.butterfly_forward_angle_offset_deg)
    turn_offset = motion_rng.uniform(-config.butterfly_frame_turn_min_deg, config.butterfly_frame_turn_min_deg)
    turn_direction = -1.0 if motion_rng.random() < 0.5 else 1.0
    frame_turns: list[float] = []
    for _ in range(slice_count):
        turn_delta = turn_direction * motion_rng.uniform(
            config.butterfly_frame_turn_min_deg,
            config.butterfly_frame_turn_max_deg,
        )
        if abs(turn_offset + turn_delta) > config.butterfly_turn_bound_deg:
            turn_direction *= -1.0
            turn_delta = turn_direction * motion_rng.uniform(
                config.butterfly_frame_turn_min_deg,
                config.butterfly_frame_turn_max_deg,
            )
        turn_offset += turn_delta
        frame_turns.append(base_rotation_angle + turn_offset)

    events: list[OutputEvent] = []
    for frame_index in range(slice_count):
        start_time = _round_ms(syl_end_abs + frame_index * step_ms)
        end_time = (
            _round_ms(syl_end_abs + tb)
            if frame_index == slice_count - 1
            else _round_ms(min(syl_end_abs + tb, start_time + step_ms))
        )
        if end_time <= start_time:
            continue

        u = clamp((start_time - syl_end_abs) / tb, 0.0, 1.0)
        arc_u = 4.0 * u * (1.0 - u)
        x = x0 + move_dx * u + arc_x * arc_u
        y = y0 + move_dy * u + arc_y * arc_u
        frame = frames[(start_frame + frame_index) % len(frames)]
        if start_time > fade_start:
            fade_u = (start_time - fade_start) / tf if tf > 0 else 1.0
            alpha = _alpha_lerp(config.butterfly_alpha, fade_u)
        else:
            fade_u = 0.0
            alpha = config.butterfly_alpha

        bounds = frame.bounds
        if bounds is None:
            continue

        divisor = _drawing_scale_divisor(frame.scale)
        center_x = ((bounds[0] + bounds[2]) / 2) / divisor * drawing_scale
        center_y = ((bounds[1] + bounds[3]) / 2) / divisor * drawing_scale
        pos_x = x - center_x
        pos_y = y - center_y
        butterfly_text = (
            f"{{\\an7\\pos({round(pos_x)},{round(pos_y)})\\org({round(x)},{round(y)})\\frz{frame_turns[frame_index]:.1f}"
            f"\\bord0\\1c{config.butterfly_color}\\1a{alpha}"
            f"{fsc}\\p{frame.scale}}}{frame.drawing}"
        )
        glow_alpha = _alpha_lerp(config.butterfly_glow_alpha, fade_u) if fade_u > 0.0 else config.butterfly_glow_alpha
        butterfly_glow_text = (
            f"{{\\an7\\pos({round(pos_x)},{round(pos_y)})\\org({round(x)},{round(y)})\\frz{frame_turns[frame_index]:.1f}"
            f"\\bord{config.butterfly_glow_border_px:g}\\blur{config.butterfly_glow_blur:g}"
            f"\\1c{config.butterfly_color}\\3c{config.butterfly_color}\\1a{glow_alpha}\\3a{glow_alpha}"
            f"{fsc}\\p{frame.scale}}}{frame.drawing}"
        )

        events.append(
            OutputEvent(
                layer=line_layer_base + 3,
                style=style_name,
                start_time=start_time,
                end_time=end_time,
                text=butterfly_glow_text,
            )
        )
        events.append(
            OutputEvent(
                layer=line_layer_base + 4,
                style=style_name,
                start_time=start_time,
                end_time=end_time,
                text=butterfly_text,
            )
        )

    return events


def _tail_release_time(stroke_proxy: object, tail_points: list[CenterPoint], tail_ctx: SylContext) -> float:
    """Return the time when the tail wipe first enters the right-side knot."""

    release_time = float(stroke_proxy.end_time)
    if len(tail_points) < 2:
        return release_time

    config = tail_ctx.config
    main_span = max(1.0, tail_points[-1].x - tail_points[0].x)
    knot_span = _estimate_right_knot_length_px(tail_ctx)
    wipe_speed = max(0.1, config.tail_knot_wipe_speed)
    wipe_span = main_span + knot_span / wipe_speed
    stroke_dt = max(1.0, stroke_proxy.end_time - stroke_proxy.start_time)
    knot_entry_ratio = clamp(main_span / max(FLOAT_EPSILON, wipe_span), 0.0, 1.0)
    release_time = _round_ms(stroke_proxy.start_time + stroke_dt * knot_entry_ratio)
    return min(stroke_proxy.end_time, max(stroke_proxy.start_time, release_time))


def _tail_anchor(
    stroke_proxy: object,
    tail_points: list[CenterPoint],
    tail_ctx: SylContext,
    release_time: float,
) -> tuple[float, float]:
    """Return the right-side tail anchor for butterfly release."""

    if len(tail_points) < 2:
        return float(stroke_proxy.right), float(stroke_proxy.middle)

    frame_index = int(
        max(
            0.0,
            (release_time - stroke_proxy.start_time) / max(1.0, _slice_step_ms(tail_ctx.config)),
        )
    )
    tail_perturbed = _perturb_centerline(tail_points, tail_ctx, release_time, frame_index)
    if not tail_perturbed:
        return float(stroke_proxy.right), float(stroke_proxy.middle)
    return tail_perturbed[-1].x, tail_perturbed[-1].y


def _tail_release_times(release_time: float, line_end_time: float, config: MeltConfig) -> list[float]:
    """Return initial and long-hold tail butterfly release times."""

    release_times = [release_time]
    long_hold_span = max(0.0, line_end_time - release_time)
    if long_hold_span <= config.line_long_hold_threshold_ms:
        return release_times

    interval = max(120, config.butterfly_long_hold_interval_ms)
    extra_count = min(
        max(0, config.butterfly_long_hold_max_extra),
        max(0, int(math.floor(long_hold_span / interval))),
    )
    release_times.extend(release_time + interval * index for index in range(1, extra_count + 1))
    return release_times


def _build_tail_butterfly_events(
    *,
    line: Line,
    stroke_proxy: object,
    syllable_id_base: int,
    tail_anchor_x: float,
    tail_anchor_y: float,
    release_times: Sequence[float],
    line_layer_base: int,
    style_name: str,
    config: MeltConfig,
) -> list[OutputEvent]:
    """Build butterfly releases attached to the stroke tail."""

    anchor_half_width = max(4.0, config.line_width_px * 1.8)
    anchor_half_height = max(4.0, config.line_width_px * 1.4)
    line_end_time = float(getattr(line, "end_time", release_times[0]))
    events: list[OutputEvent] = []

    for release_index, release_time in enumerate(release_times):
        if release_time >= line_end_time:
            continue
        release_proxy = SimpleNamespace(
            # Vary the pseudo syllable id so each long-hold butterfly keeps normal randomness.
            i=syllable_id_base + 100 + release_index,
            left=tail_anchor_x - anchor_half_width,
            right=tail_anchor_x + anchor_half_width,
            top=tail_anchor_y - anchor_half_height,
            middle=tail_anchor_y,
            bottom=tail_anchor_y + anchor_half_height,
            start_time=stroke_proxy.start_time,
            end_time=_round_ms(release_time),
        )
        events.extend(
            _build_spike_events(
                line=line,
                syl=release_proxy,
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
            )
        )
    return events


def melt_line(
    line,
    line_layer_base: int,
    config: MeltConfig,
    style_name: str = "p",
    y_offset: float = 0.0,
) -> list[OutputEvent]:
    """Assemble all effect events for one line."""

    events: list[OutputEvent] = []
    syllables = list(Utils.all_non_empty(line.syls, progress_bar=False))
    if not syllables:
        return events

    if y_offset != 0.0:
        for syl in syllables:
            _shift_syl_geometry(syl, y_offset)

    stroke_end_time = _effective_stroke_end_time(line, syllables, config)
    stroke_proxy = SimpleNamespace(
        i=syllables[0].i,
        left=getattr(line, "left", min(syl.left for syl in syllables)),
        right=getattr(line, "right", max(syl.right for syl in syllables)),
        top=min(syl.top for syl in syllables),
        middle=getattr(line, "middle", max(syl.middle for syl in syllables)),
        bottom=max(syl.bottom for syl in syllables),
        start_time=line.start_time,
        end_time=stroke_end_time,
    )
    layer_shapes = text_to_layer_shapes(stroke_proxy, config)
    if layer_shapes:
        events.extend(
            _build_vector_mask_events(
                line=line,
                syl=stroke_proxy,
                layers=layer_shapes,
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
            )
        )
        # Release one extra butterfly when wipe enters the right-side knot
        # (slightly earlier than full disappearance).
        tail_ctx = _build_syl_context(line, stroke_proxy, config)
        tail_points = _sample_centerline(tail_ctx)
        tail_release_end_time = _tail_release_time(stroke_proxy, tail_points, tail_ctx)
        tail_anchor_x, tail_anchor_y = _tail_anchor(stroke_proxy, tail_points, tail_ctx, tail_release_end_time)
        long_hold_end = float(getattr(line, "end_time", tail_release_end_time))
        events.extend(
            _build_tail_butterfly_events(
                line=line,
                stroke_proxy=stroke_proxy,
                syllable_id_base=syllables[-1].i,
                tail_anchor_x=tail_anchor_x,
                tail_anchor_y=tail_anchor_y,
                release_times=_tail_release_times(tail_release_end_time, long_hold_end, config),
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
            )
        )

    max_gap = max(max(1, config.butterfly_min_syllable_gap), config.butterfly_max_syllable_gap)
    syllables_since_butterfly = _noise_seed(config.random_seed + line.i, 701).randrange(0, max_gap)
    emitted_first_butterfly = False
    for syl in syllables:
        if syl.right - syl.left < 2:
            continue

        syllables_since_butterfly += 1
        butterfly_rng = _noise_seed(_syl_seed(config, line, syl), 607)
        should_emit = not emitted_first_butterfly or _should_emit_butterfly(
            gap=syllables_since_butterfly,
            config=config,
            rng=butterfly_rng,
        )
        if not should_emit:
            continue

        emitted_first_butterfly = True
        syllables_since_butterfly = 0
        events.extend(
            _build_spike_events(
                line=line,
                syl=syl,
                line_layer_base=line_layer_base,
                style_name=style_name,
                config=config,
            )
        )

    return events


def _write_original_lines(io: Ass, lines: list[Line]) -> None:
    """Write original subtitle lines back as commented ASS events."""

    time_cache: dict[float, str] = {}
    serialized: list[str] = []
    for line in lines:
        serialized.append(
            _format_ass_event(
                event_kind="Comment",
                layer=int(getattr(line, "layer", 0)),
                start_text=_cached_ass_timestamp(time_cache, line.start_time),
                end_text=_cached_ass_timestamp(time_cache, line.end_time),
                style=line.style,
                actor=line.actor,
                margin_l=f"{line.margin_l:04d}",
                margin_r=f"{line.margin_r:04d}",
                margin_v=f"{line.margin_v:04d}",
                effect=line.effect,
                text=line.raw_text,
            )
        )

    io._output.extend(serialized)
    io._plines += len(serialized)


def _process_line_worker(args: tuple[Line, int, MeltConfig, str, float]) -> list[OutputEvent]:
    """Render one copied line inside a worker."""

    line, line_layer_base, config, style_name, y_offset = args
    return melt_line(line, line_layer_base, config, style_name=style_name, y_offset=y_offset)


def _write_output_events(io: Ass, template_line: Line, events: list[OutputEvent]) -> None:
    """Serialize generated events into the ASS output buffer."""

    if not events:
        return

    event_kind = "Comment" if template_line.comment else "Dialogue"
    actor = template_line.actor
    margin_l = f"{template_line.margin_l:04d}"
    margin_r = f"{template_line.margin_r:04d}"
    margin_v = f"{template_line.margin_v:04d}"
    effect = template_line.effect
    time_cache: dict[float, str] = {}

    serialized: list[str] = []
    append_serialized = serialized.append
    for event in events:
        append_serialized(
            _format_ass_event(
                event_kind=event_kind,
                layer=event.layer,
                start_text=_cached_ass_timestamp(time_cache, event.start_time),
                end_text=_cached_ass_timestamp(time_cache, event.end_time),
                style=event.style,
                actor=actor,
                margin_l=margin_l,
                margin_r=margin_r,
                margin_v=margin_v,
                effect=effect,
                text=event.text,
            )
        )

    io._output.extend(serialized)
    io._plines += len(serialized)


def _compute_collision_offsets(target_lines: list[tuple[int, Line]]) -> dict[int, float]:
    """Apply Aegisub-like collision avoidance for bottom-aligned lines."""

    offsets: dict[int, float] = {}
    placed: list[tuple[int, int, float, float]] = []

    for _, line in target_lines:
        if line.styleref.alignment not in (1, 2, 3):
            continue

        line_height = getattr(line, "height", 0) or 0
        if line_height <= 0:
            continue

        my_top = line.top
        my_bottom = my_top + line_height

        shift = 0.0
        for p_start, p_end, p_top, p_bottom in placed:
            if line.start_time < p_end and p_start < line.end_time:
                cur_top = my_top - shift
                cur_bottom = my_bottom - shift
                if cur_bottom > p_top and cur_top < p_bottom:
                    shift += cur_bottom - p_top

        if shift > 0:
            offsets[line.i] = -shift

        placed.append((line.start_time, line.end_time, my_top - shift, my_bottom - shift))

    return offsets


def _should_use_multiprocessing(target_lines: list[tuple[int, Line]], config: MeltConfig) -> bool:
    """Decide whether multiprocessing should be used."""

    return config.enable_multiprocessing and len(target_lines) >= config.multiprocessing_min_lines


def _parse_bool_arg(value: str) -> bool:
    """Parse flexible boolean CLI values."""

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {value!r}")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(description="Render the redline syllable effect into an ASS file.")
    parser.add_argument("--input", default="in.ass", help="Input ASS path.")
    parser.add_argument("--output", default="output.ass", help="Output ASS path.")
    parser.add_argument("--style-name", default="p", help="Generated effect style name.")
    parser.add_argument("--keep-original", type=_parse_bool_arg, default=True, help="Keep original dialogue lines.")
    parser.add_argument("--extended", type=_parse_bool_arg, default=True, help="Whether Ass should compute extended line data.")
    parser.add_argument(
        "--effect-mode",
        choices=("combined", "melt", "word"),
        default="combined",
        help="Select the rendering pipeline.",
    )
    parser.add_argument(
        "--word-root",
        default=os.path.join(os.path.dirname(__file__), "word"),
        help="Word effect asset root path.",
    )

    for config_field in fields(MeltConfig):
        parser.add_argument(
            f"--{config_field.name.replace('_', '-')}",
            dest=config_field.name,
            type=_parse_bool_arg if isinstance(config_field.default, bool) else type(config_field.default),
            default=None,
            help=f"Override MeltConfig.{config_field.name} (default: {config_field.default!r}).",
        )

    return parser


def build_config_from_args(args: argparse.Namespace) -> MeltConfig:
    """Construct MeltConfig from CLI overrides."""

    overrides = {
        config_field.name: value
        for config_field in fields(MeltConfig)
        if (value := getattr(args, config_field.name)) is not None
    }
    return MeltConfig(**overrides)


def _configure_stdio_for_windows() -> None:
    """Force UTF-8 stdio when possible on Windows."""

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
    effect_mode: str = "melt",
    word_root: str | None = None,
) -> str:
    """Render the redline effect and save the ASS output."""

    _configure_stdio_for_windows()
    config = config or MeltConfig()

    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)

    io = Ass(input_path, output_path, keep_original=False, extended=extended)
    _, _, lines = io.get_data()
    io.add_style(style_name, Ass.PIXEL_STYLE)
    if keep_original:
        _write_original_lines(io, lines)

    target_lines = _select_target_lines(lines)
    if not target_lines:
        io.save()
        return io.path_output

    collision_offsets = _compute_collision_offsets(target_lines)

    if effect_mode in {"word", "combined"}:
        bridge = create_word_fx_bridge(
            word_root=word_root or os.path.join(os.path.dirname(__file__), "word"),
            layers_per_line=LAYERS_PER_LINE,
        )
        bridge.render_target_lines(
            io,
            target_lines,
            line_preparer=lambda line, _line_index: shift_word_fx_line(
                line,
                collision_offsets.get(line.i, 0.0),
            ),
        )
        if effect_mode == "word":
            io.save()
            return io.path_output

    work_items = [
        (
            line.copy(),
            max(int(getattr(line, "layer", 0)) + 10, 10) + line_index * LAYERS_PER_LINE,
            config,
            style_name,
            collision_offsets.get(line.i, 0.0),
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
    """CLI entry point."""

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
        effect_mode=args.effect_mode,
        word_root=args.word_root,
    )


if __name__ == "__main__":
    main()
