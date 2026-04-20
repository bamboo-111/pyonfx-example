"""ASS 字幕逐字 KTV 特效生成器 V3（v1 基础 + v2 入场散开/抖动/高斯模糊）

改自 v1：
- Entry: 每字从随机方向随机距离的位置 \\move 归位 + 白色淡入 → 样式色
- Sustain: v1 的脉冲（放大+变模糊+渐隐+回弹）+ v2 的 fscy 抖动 + 高斯运动模糊副本
- Exit: 与 v1 一致（偏移变红渐隐）

Usage:
    python ass_char_fx_v3.py input.ass
    python ass_char_fx_v3.py input.ass -o out.ass --offset-dist 30
"""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Sequence

from pyonfx import Ass, Char, Utils, Shape, Convert
from shapely import affinity as _shp_affinity
from shapely.geometry import MultiPolygon, Polygon, GeometryCollection
from shapely.ops import unary_union
from shapely.validation import make_valid


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CharFxConfig:
    # --- Entry 入场 ---
    char_delay_ms: int = 30              # 逐字入场间隔（毫秒），0=全字同时出现
    entry_lead_ms: int = 150            # 整行提前量：第一个字在 line_start 之前多少 ms 开始入场
    fade_in_ms: int = 100               # 每个字白色淡入到样式色的时长（毫秒）
    entry_spread_ratio: float = 0.3    # 入场散开基础距离 = ratio × 字号；0=不散开
    entry_spread_jitter: float = 0.45   # 散开距离随机抖动 ±jitter

    # --- Sustain 脉冲（K 时间段内） ---
    sustain_peak_scale: float = 120.0   # 脉冲峰值放大百分比，120=放大到 120%
    sustain_peak_bord: float = 1.5      # 脉冲峰值描边粗细（像素）
    sustain_peak_blur: float = 20       # 脉冲峰值模糊半径（像素）
    sustain_base_blur: float = 6.0      # 非脉冲时的常驻模糊（像素）
    sustain_alpha_dip: int = 100        # 脉冲时短暂渐隐的 alpha，0=不透明 255=全透明
    sustain_min_ms: int = 120           # K 时间短于此值则跳过脉冲（毫秒）
    sustain_speed: float = 1          # 脉冲速度倍率
    sustain_min_pulses: float = 3   # 短字最少占用几个脉冲周期（避免抖一下就没了）
    sync_pulse: bool = True             # 锚定到全局 0ms 节拍

    # --- v2 抖动叠加（在脉冲峰值附近的 fscy + bord + blur 抖动）---
    shake_enabled: bool = True
    shake_period_ms: int = 40           # 单次抖动周期
    shake_fscy_delta: float = 18.0      # fscy 增量（峰 = 100 + delta）
    shake_blur_ratio: float = 0.30      # 抖动 blur 峰 = ratio × 字号
    shake_bord_ratio: float = 0.1      # 抖动 bord 峰 = ratio × 字号

    # --- v2 高斯运动模糊副本（节拍点叠加多个副本模拟运动模糊）---
    motion_blur_enabled: bool = True
    motion_blur_samples: int = 21        # 副本数量（奇数）
    motion_blur_dist_ratio: float = 0.8 # 总跨距 = ratio × 字号
    motion_blur_sigma_ratio: float = 0.5 # 高斯 σ 占半跨距比例
    motion_blur_blur_ratio: float = 0.5 # 边缘副本最大额外模糊 = ratio × 字号
    motion_blur_alpha_center: int = 220   # 中心副本透明度 0-255（小=显）
    motion_blur_alpha_edge: int = 254    # 边缘副本透明度 0-255（大=隐）
    motion_blur_flash_ms: int = 110      # 副本可见窗口
    motion_blur_layer_delta: int = 1    # 副本相对主层 layer 偏移

    # --- 噪声遮罩 ---
    noise_mask_enabled: bool = False
    noise_mask_alpha: int = 0
    noise_mask_scale: float = 3
    noise_mask_steps: int = 1

    # --- Exit 出场 ---
    offset_dist: float = 16
    offset_dur_ms: int = 1300
    fade_out_ms: int = 0
    exit_stagger_max_ms: int = 100

    # --- 出场抖动叠加（红色字体在退场时持续抖动；振幅逐渐变大；alpha 与字体淡出复合）---
    exit_shake_enabled: bool = True      # 总开关；False = 退回原平滑淡出
    exit_shake_period_ms: int = 100       # 抖动周期
    exit_shake_amp_growth: float = 2.0   # 振幅增长系数（末尾 = 初始 × (1+growth)）
    exit_shake_fscy_delta: float = 12.0  # 起始 fscy 增量，末尾 × (1+growth)
    exit_shake_blur_ratio: float = 0.15  # 起始 blur = ratio × 字号，末尾 × (1+growth)
    exit_shake_bord_ratio: float = 0.05  # 起始 bord = ratio × 字号，末尾 × (1+growth)
    exit_shake_alpha_amp: int = 80       # 抖动增加的 alpha 透明量（在字体本身 alpha 上叠加）
    exit_color_ratio: float = 0.15       # 颜色变红时长占出场总时长的比例（小=红得快，1.0=全程线性插值）

    # --- 其他 ---
    inject_tags: str = ""
    random_seed: int = 12345


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIRECTIONS_8 = [
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (-1, 1), (1, -1), (-1, -1),
]


def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _ms_to_ass(ms: int) -> str:
    ms = max(0, int(ms))
    h = ms // 3600000; ms %= 3600000
    m = ms // 60000;   ms %= 60000
    s = ms // 1000;    cs = (ms % 1000) // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _style_colour(styleref) -> str:
    try:
        color = styleref.color1.strip()
        alpha = styleref.alpha1.strip()
        c_hex = re.sub(r"[^0-9A-Fa-f]", "", color).zfill(6)[-6:]
        a_hex = re.sub(r"[^0-9A-Fa-f]", "", alpha).zfill(2)[-2:]
        return f"&H{a_hex}{c_hex}&"
    except AttributeError:
        return "&H00FFFFFF&"


def _outline_colour(styleref) -> str:
    try:
        color = styleref.color3.strip()
        alpha = styleref.alpha3.strip()
        c_hex = re.sub(r"[^0-9A-Fa-f]", "", color).zfill(6)[-6:]
        a_hex = re.sub(r"[^0-9A-Fa-f]", "", alpha).zfill(2)[-2:]
        return f"&H{a_hex}{c_hex}&"
    except AttributeError:
        return "&H00000000&"


def _extract_style_tags(text: str) -> str:
    merged = "".join(re.findall(r"\{([^}]*)\}", text))
    for pat in (
        r"\\pos\([^)]*\)",
        r"\\move\([^)]*\)",
        r"\\org\([^)]*\)",
        r"\\alpha&H[0-9A-Fa-f]+&",
        r"\\1a&H[0-9A-Fa-f]+&",
        r"\\an\d",
        r"\\k[fo]?\d+",
        r"\\t\([^)]*\)",
        r"=\d+",
        r"\\blur[\d.]+",
    ):
        merged = re.sub(pat, "", merged)
    return merged.strip()


# ---------------------------------------------------------------------------
# Geometry helpers — repair invalid polygons before handing to Shape
# ---------------------------------------------------------------------------

def _to_multipolygon(geom) -> MultiPolygon:
    if geom is None or geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, GeometryCollection):
        polys = [g for g in geom.geoms if isinstance(g, Polygon) and not g.is_empty]
        return MultiPolygon(polys) if polys else MultiPolygon()
    return MultiPolygon()


def _repair(geom) -> MultiPolygon:
    """Make geometry valid; return MultiPolygon (possibly empty)."""
    if geom is None or geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, (Polygon, MultiPolygon)) and geom.is_valid:
        return _to_multipolygon(geom)
    try:
        fixed = make_valid(geom)
    except Exception:
        try:
            fixed = geom.buffer(0)
        except Exception:
            return MultiPolygon()
    return _to_multipolygon(fixed)


def _multipolygon_to_ass_drawing(mp: MultiPolygon) -> str:
    if mp is None or mp.is_empty:
        return ""
    mp = _repair(mp)
    if mp.is_empty:
        return ""
    try:
        return str(Shape.from_multipolygon(mp, min_point_spacing=0.75))
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Noise mask generation (cached unit-square template, scaled per glyph)
# ---------------------------------------------------------------------------

def _worley_2d(x: float, y: float, seed: int, point_density: int = 4) -> float:
    cx = math.floor(x); cy = math.floor(y)
    best = float("inf")
    for dx in range(-1, 2):
        for dy in range(-1, 2):
            nx = cx + dx; ny = cy + dy
            for p in range(point_density):
                h = ((nx * 1836311903) ^ (ny * 2971215073) ^
                     (seed * 4807526976) ^ (p * 16777619)) & 0xFFFFFFFF
                px = nx + ((h & 0xFFFF) / 0xFFFF)
                py = ny + (((h >> 16) & 0xFFFF) / 0xFFFF)
                d = math.hypot(x - px, y - py)
                if d < best:
                    best = d
    return best


def _fbm_worley(x: float, y: float, seed: int, octaves: int) -> float:
    total = 0.0; amp = 1.0; freq = 1.0; norm = 0.0
    for o in range(max(1, octaves)):
        total += _worley_2d(x * freq, y * freq, seed + o * 1013) * amp
        norm += amp; amp *= 0.5; freq *= 2.0
    return total / norm if norm > 0 else 0.0


_NOISE_TEMPLATE_CACHE: dict[tuple, list[MultiPolygon]] = {}


def _noise_template(*, scale: float, steps: int = 25,
                    resolution: int = 18, octaves: int = 3,
                    seed: int = 42) -> list[MultiPolygon]:
    """Generate noise mask bands in unit square [0,1]² (cached)."""
    key = (round(scale, 3), steps, resolution, octaves, seed)
    cached = _NOISE_TEMPLATE_CACHE.get(key)
    if cached is not None:
        return cached

    grid = resolution
    cell = 1.0 / grid
    cells: list[tuple[float, Polygon]] = []
    for row in range(grid):
        for col in range(grid):
            x0 = col * cell
            y0 = row * cell
            sx = ((col + 0.5) / grid) * scale
            sy = ((row + 0.5) / grid) * scale
            v = _fbm_worley(sx, sy, seed, octaves)
            cells.append((v, Polygon([
                (x0,       y0),
                (x0+cell, y0),
                (x0+cell, y0+cell),
                (x0,       y0+cell),
            ])))

    cells.sort(key=lambda c: c[0])
    total = len(cells)
    bands: list[MultiPolygon] = []
    for step in range(steps):
        i0 = int(round((step / steps) * total))
        i1 = int(round(((step + 1) / steps) * total))
        polys = [p for _, p in cells[i0:i1]]
        if not polys:
            bands.append(MultiPolygon())
        else:
            bands.append(_repair(unary_union(polys)))

    _NOISE_TEMPLATE_CACHE[key] = bands
    return bands


def _scale_template_to_bounds(
    template: Sequence[MultiPolygon],
    bounds: tuple[float, float, float, float],
) -> list[MultiPolygon]:
    min_x, min_y, max_x, max_y = bounds
    w = max(1.0, max_x - min_x)
    h = max(1.0, max_y - min_y)
    out: list[MultiPolygon] = []
    for band in template:
        if band.is_empty:
            out.append(MultiPolygon())
            continue
        scaled = _shp_affinity.scale(band, xfact=w, yfact=h, origin=(0.0, 0.0))
        scaled = _shp_affinity.translate(scaled, xoff=min_x, yoff=min_y)
        out.append(_repair(scaled))
    return out


# ---------------------------------------------------------------------------
# Sustain pulse animation tags
# ---------------------------------------------------------------------------

def _sustain_tags(dur_ms: int, cfg: CharFxConfig, base_bord: float,
                   base_outline_colour: str = "&H00000000&",
                   allow_repeat: bool = False) -> str:
    if dur_ms < cfg.sustain_min_ms:
        return f"\\blur{_fmt(cfg.sustain_base_blur)}"

    # 单次脉冲时长 = sustain_min_ms / speed（固定基准 × 速度倍率）
    # 普通字至少跑 sustain_min_pulses 次；允许重复的字按整段时长尽量填满
    speed   = max(0.1, cfg.sustain_speed)
    pulse_w = max(1, int(cfg.sustain_min_ms / speed))
    min_repeats = max(1, int(round(cfg.sustain_min_pulses)))
    if allow_repeat:
        repeats = max(min_repeats, dur_ms // pulse_w)
    else:
        repeats = min_repeats

    ps        = _fmt(cfg.sustain_peak_scale)
    pb        = _fmt(cfg.sustain_peak_bord)
    bb        = _fmt(base_bord)
    pk_blur   = _fmt(cfg.sustain_peak_blur)
    base_blur = _fmt(cfg.sustain_base_blur)
    da        = f"&H{cfg.sustain_alpha_dip:02X}&"
    peak_oc   = "&H000000FF&"            # 脉冲峰值描边变红
    base_oc   = base_outline_colour      # 回弹时还原原描边色

    parts = [f"\\blur{base_blur}"]
    sh_period = max(20, cfg.shake_period_ms)

    # ---- 1. 主脉冲循环：放大 + 加粗 + 模糊 + alpha dip + 平滑回落 ----
    for r in range(repeats):
        base = r * pulse_w
        pk  = base + int(pulse_w * 0.35)
        mid = base + int(pulse_w * 0.55)
        end = base + pulse_w
        parts.append(
            f"\\t({base},{pk},\\fscx{ps}\\fscy{ps}\\bord{pb}\\blur{pk_blur}\\alpha{da}\\3c{peak_oc})"
            f"\\t({pk},{mid},\\alpha&H00&)"
            f"\\t({pk},{end},\\fscx100\\fscy100\\bord{bb}\\blur{base_blur}\\3c{base_oc})"
        )

    # ---- 2. 持续抖动叠加：覆盖整个 sustain，恒定振幅在 fscy/bord/blur 上叠加 ----
    if cfg.shake_enabled and dur_ms >= sh_period * 2:
        n_sh = max(1, dur_ms // sh_period)
        # 抖动峰值（按字号比例 / 不依赖 sustain_peak_blur）
        peak_blur_unit = cfg.sustain_peak_blur if cfg.sustain_peak_blur > 0 else 50
        sh_fscy = _fmt(100.0 + cfg.shake_fscy_delta)
        sh_blur = _fmt(cfg.shake_blur_ratio * peak_blur_unit)
        sh_bord = _fmt(max(base_bord, cfg.shake_bord_ratio * peak_blur_unit))
        for s in range(n_sh):
            t0    = int(dur_ms * (s / n_sh))
            t_mid = int(dur_ms * ((s + 0.5) / n_sh))
            t_end = int(dur_ms * ((s + 1) / n_sh))
            parts.append(
                f"\\t({t0},{t_mid},\\fscy{sh_fscy}\\bord{sh_bord}\\blur{sh_blur})"
                f"\\t({t_mid},{t_end},\\fscy100\\bord{bb}\\blur{base_blur})"
            )
    return "".join(parts)


_T_RE = re.compile(r"\\t\((-?\d+),(-?\d+),([^)]*)\)")


def _shift_anim_tags(anim: str, shift_ms: int) -> str:
    """Shift every \\t(t1,t2,...) inside an anim string by shift_ms."""
    def repl(m: re.Match) -> str:
        t1 = int(m.group(1)) + shift_ms
        t2 = int(m.group(2)) + shift_ms
        return f"\\t({t1},{t2},{m.group(3)})"
    return _T_RE.sub(repl, anim)


# ---------------------------------------------------------------------------
# ASS pre-processing: inject missing styles
# ---------------------------------------------------------------------------

def _patch_missing_styles(ass_text: str) -> str:
    defined = {m.group(1).split(",")[0].strip()
               for m in re.finditer(r"^Style:\s*(.+)", ass_text, re.MULTILINE)}
    used = {m.group(1).strip()
            for m in re.finditer(
                r"^(?:Dialogue|Comment):[^,]*,[^,]*,[^,]*,([^,]+),",
                ass_text, re.MULTILINE)}
    missing = used - defined
    if not missing:
        return ass_text
    template_m = re.search(r"^(Style: Default,.+)$", ass_text, re.MULTILINE)
    template = template_m.group(1) if template_m else (
        "Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1"
    )
    new_styles = []
    for name in sorted(missing):
        parts = template.split(",")
        parts[0] = f"Style: {name}"
        new_styles.append(",".join(parts))
    return ass_text.replace("[Events]", "\n".join(new_styles) + "\n[Events]", 1)


def _prepare_input(input_path: str) -> str:
    with open(input_path, encoding="utf-8-sig") as f:
        content = f.read()
    patched = _patch_missing_styles(content)
    if patched == content:
        return input_path
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ass", encoding="utf-8", delete=False)
    tmp.write(patched)
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# Collision avoidance (ported from spike.py)
# ---------------------------------------------------------------------------

def _compute_collision_offsets(lines, style_order: dict[str, int] | None = None) -> dict[int, float]:
    offsets: dict[int, float] = {}
    placed: list[tuple[int, int, float, float]] = []

    # 排序：style_order 中索引大的（[V4+ Styles] 中靠后定义的）先处理 → 占本位（下方）
    #       索引小的（靠前定义的）后处理 → 被推上去（上方）
    # 所以 Aegisub 中越靠前定义的样式，最终越在屏幕上方
    style_order = style_order or {}

    def _sort_key(L):
        style = getattr(L, 'style', '') or ''
        idx = style_order.get(style, 10**6)
        # 倒序索引：大的排前（处理在前 = 留本位 = 屏幕下方）
        return (L.start_time, -idx, style, getattr(L, 'i', 0))

    sorted_lines = sorted(lines, key=_sort_key)

    for line in sorted_lines:
        if line.comment:
            continue
        try:
            if line.styleref.alignment not in (1, 2, 3):
                continue
        except AttributeError:
            continue

        h = getattr(line, 'height', 0) or 0
        if h <= 0:
            continue

        my_top    = line.top
        my_bottom = my_top + h
        shift     = 0.0

        for p_start, p_end, p_top, p_bottom in placed:
            if line.start_time < p_end and p_start < line.end_time:
                if (my_bottom - shift) > p_top and (my_top - shift) < p_bottom:
                    shift += (my_bottom - shift) - p_top

        if shift > 0:
            offsets[line.i] = -shift

        placed.append((line.start_time, line.end_time,
                        my_top - shift, my_bottom - shift))
    return offsets


# ---------------------------------------------------------------------------
# Glyph outline cache (avoid re-tessellating identical char-style pairs)
# ---------------------------------------------------------------------------

def _glyph_outline(ch: Char, y_offset: float) -> MultiPolygon | None:
    """Return absolute-coordinate MultiPolygon for the glyph outline, or None."""
    try:
        glyph_shape = Convert.text_to_shape(ch).move(ch.left % 1, ch.top % 1)
        mp = glyph_shape.to_multipolygon()
        mp = _shp_affinity.translate(
            mp,
            xoff=math.floor(ch.left),
            yoff=math.floor(ch.top) + y_offset,
        )
        return _repair(mp) if mp else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Character event generation
# ---------------------------------------------------------------------------

def _build_char_events(line, cfg: CharFxConfig,
                        rng: random.Random, y_offset: float = 0.0) -> list[str]:
    line_start = int(line.start_time)
    line_end   = int(line.end_time)

    extra_tags = _extract_style_tags(line.raw_text)
    if cfg.inject_tags:
        extra_tags = extra_tags + cfg.inject_tags

    style_colour   = _style_colour(line.styleref)
    outline_colour = _outline_colour(line.styleref)

    try:
        base_bord = float(line.styleref.outline)
    except (AttributeError, ValueError):
        base_bord = 0.5

    # 提取实际字号（优先 inline \fs 覆盖，否则 styleref 字号）
    fs_match = re.search(r"\\fs(\d+(?:\.\d+)?)", line.raw_text)
    if fs_match:
        font_size = float(fs_match.group(1))
    else:
        try:
            font_size = float(line.styleref.fontsize)
        except (AttributeError, ValueError):
            font_size = 48.0

    chars: list[Char] = list(Utils.all_non_empty(line.chars, progress_bar=False))
    if not chars:
        return []

    actor = line.actor
    ml    = f"{line.margin_l:04d}"
    mr    = f"{line.margin_r:04d}"
    mv    = f"{line.margin_v:04d}"
    eff   = line.effect
    style = line.style
    layer = line.layer

    out: list[str] = []
    base_blur_tag = f"\\blur{_fmt(cfg.sustain_base_blur)}"

    # Get noise template once per line (shared across all chars)
    noise_template = None
    if cfg.noise_mask_enabled:
        noise_template = _noise_template(scale=cfg.noise_mask_scale, steps=cfg.noise_mask_steps)

    for i, ch in enumerate(chars):
        cx = _fmt(ch.center)
        cy = _fmt(ch.middle + y_offset)

        char_show = min(line_start + int(ch.start_time), line_end - 1)
        char_end  = min(line_start + int(ch.end_time),   line_end)

        # 后面是空格（拖长音）时，把空格段 K 时间也吃掉，让脉冲持续到拖音结束
        is_last = (i == len(chars) - 1)
        next_is_space = (not is_last) and (not chars[i + 1].text.strip())
        if next_is_space:
            j = i + 1
            while j < len(chars) and not chars[j].text.strip():
                char_end = min(line_start + int(chars[j].end_time), line_end)
                j += 1
        allow_repeat = is_last or next_is_space

        # 固定字间隔 + 固定入场时长（滑动 = 淡入 = fade_in_ms）
        entry_start = max(0, line_start - max(0, cfg.entry_lead_ms) + i * cfg.char_delay_ms)

        # 保证至少跑完 sustain_min_pulses 个完整脉冲周期
        speed = max(0.1, cfg.sustain_speed)
        pulse_w = max(1, int(cfg.sustain_min_ms / speed))
        min_sustain_ms = int(pulse_w * max(1.0, cfg.sustain_min_pulses))
        sustain_safe_end = max(char_end, char_show + min_sustain_ms)
        sustain_dur = sustain_safe_end - char_show

        # 同步脉冲：先算出延迟，再用扣掉延迟的时长生成动画
        entry_dur = max(0, char_show - entry_start)
        sync_delay = 0
        if cfg.sync_pulse and sustain_dur > 0:
            sync_delay = (-char_show) % pulse_w
            if sync_delay + pulse_w > sustain_dur:
                sync_delay = 0

        effective_dur = sustain_dur - sync_delay
        anim = _sustain_tags(effective_dur, cfg, base_bord, outline_colour,
                              allow_repeat=allow_repeat) if effective_dur > 0 else ""

        total_shift = entry_dur + sync_delay
        if anim and total_shift > 0:
            anim = _shift_anim_tags(anim, total_shift)

        # 出场起点：延后到 sustain_safe_end 之后 + 随机 stagger；也用于消除主 dialogue 和出场之间的空窗
        exit_delay = rng.randint(0, max(0, cfg.exit_stagger_max_ms))
        exit_start = sustain_safe_end + exit_delay

        # ---- 决定是否使用噪声切片 drawing ----
        glyph_mp = _glyph_outline(ch, y_offset) if cfg.noise_mask_enabled else None

        # pieces: [(drawing_str, alpha_int)]，用于 sustain 和 exit 复用
        pieces: list[tuple[str, int]] = []
        gcx = gcy = 0.0

        # alpha=0 → 遮罩无效果，直接走文本渲染路径
        if cfg.noise_mask_alpha <= 0:
            glyph_mp = None

        if glyph_mp and not glyph_mp.is_empty and noise_template:
            gx0, gy0, gx1, gy1 = glyph_mp.bounds
            gcx = (gx0 + gx1) * 0.5
            gcy = (gy0 + gy1) * 0.5

            bands = _scale_template_to_bounds(noise_template, glyph_mp.bounds)
            n_bands = len(bands)

            for idx, band_mp in enumerate(bands):
                if band_mp.is_empty:
                    continue
                # idx=0（最暗）→ 最透明（a_int = noise_mask_alpha）
                # idx=n-1（最亮）→ 最不透明（a_int = 0）
                t = idx / max(1, n_bands - 1)                 # 0..1
                a_int = int(round(cfg.noise_mask_alpha * (1.0 - t)))
                a_int = max(0, min(255, a_int))
                try:
                    clipped = glyph_mp.intersection(band_mp)
                except Exception:
                    try:
                        clipped = glyph_mp.buffer(0).intersection(band_mp.buffer(0))
                    except Exception:
                        continue
                clipped = _repair(clipped)
                if clipped.is_empty:
                    continue
                centered = _shp_affinity.translate(clipped, xoff=-gcx, yoff=-gcy)
                drawing = _multipolygon_to_ass_drawing(_repair(centered))
                if not drawing:
                    continue
                pieces.append((drawing, a_int))

        if pieces:
            # ---- 1+2. Entry + Sustain：每个切片一行 ----
            for drawing, a_int in pieces:
                alpha_tag    = f"&H{a_int:02X}&"
                hidden_alpha = "&HFF&"
                fade_in_t = (
                    f"\\t(0,{cfg.fade_in_ms},\\alpha{alpha_tag}\\1c{style_colour})"
                )
                piece_text = (
                    f"{{\\p1\\an5\\pos({_fmt(gcx)},{_fmt(gcy)})"
                    f"\\1c&H00FFFFFF&\\3c{style_colour}"
                    f"\\alpha{hidden_alpha}{base_blur_tag}"
                    f"{fade_in_t}{anim}"
                    f"\\bord0\\shad0"
                    f"}}{drawing}"
                )
                out.append(
                    f"Dialogue: {layer},{_ms_to_ass(entry_start)},{_ms_to_ass(char_end)},"
                    f"{style},{actor},{ml},{mr},{mv},{eff},{piece_text}"
                )
        else:
            # ---- 无噪声 / 字形提取失败 → 合并 entry + sustain 单 dialogue ----
            # 入场移动：固定 fade_in_ms 时长（滑动时长 = 淡入时长）
            if cfg.entry_spread_ratio > 0:
                ang  = rng.uniform(0, math.tau)
                dist = font_size * cfg.entry_spread_ratio * (
                    1.0 + rng.uniform(-cfg.entry_spread_jitter, cfg.entry_spread_jitter)
                )
                sx = ch.center + math.cos(ang) * dist
                sy = ch.middle + y_offset + math.sin(ang) * dist
                entry_pos_tag = (
                    f"\\move({_fmt(sx)},{_fmt(sy)},{cx},{cy},0,{cfg.fade_in_ms})"
                )
            else:
                entry_pos_tag = f"\\pos({cx},{cy})"

            head = (
                f"\\an5{entry_pos_tag}"
                f"\\alpha&HFF&\\1c&H00FFFFFF&{base_blur_tag}"
                f"\\t(0,{cfg.fade_in_ms},\\alpha&H00&\\1c{style_colour})"
                f"{anim}"
                f"{extra_tags}"
            )
            # dialogue 跨 entry_start → exit_start（无空窗：主 dialogue 恰好接到出场开始）
            dlg_end = max(entry_start + cfg.fade_in_ms, exit_start)
            out.append(
                f"Dialogue: {layer},{_ms_to_ass(entry_start)},{_ms_to_ass(dlg_end)},"
                f"{style},{actor},{ml},{mr},{mv},{eff},{{{head}}}{ch.text}"
            )

        # ---- 2.5. 高斯运动模糊副本：仅在脉冲峰值 → 回落段出现（每个脉冲一次） ----
        if cfg.motion_blur_enabled and sustain_dur >= cfg.sustain_min_ms:
            speed = max(0.1, cfg.sustain_speed)
            pulse_w = max(1, int(cfg.sustain_min_ms / speed))
            min_repeats = max(1, int(round(cfg.sustain_min_pulses)))
            if allow_repeat:
                n_beats = max(min_repeats, sustain_dur // pulse_w)
            else:
                n_beats = min_repeats

            n = max(3, cfg.motion_blur_samples)
            half_idx   = (n - 1) / 2.0
            total_dist = cfg.motion_blur_dist_ratio * font_size
            spacing    = total_dist / max(1, n - 1)
            sigma      = max(0.1, cfg.motion_blur_sigma_ratio * half_idx)
            max_blur   = cfg.motion_blur_blur_ratio * font_size
            a_center   = max(0, min(0xFF, cfg.motion_blur_alpha_center))
            a_edge     = max(0, min(0xFF, cfg.motion_blur_alpha_edge))

            samples = []
            for ii in range(n):
                idx = ii - half_idx
                if abs(idx) < 1e-6:
                    continue
                y_off = idx * spacing
                w     = math.exp(-0.5 * (idx / sigma) ** 2)
                a_int = int(round(a_edge - (a_edge - a_center) * w))
                a_int = max(0, min(0xFF, a_int))
                blur_val = (abs(idx) / half_idx) * max_blur
                samples.append((y_off, a_int, blur_val))

            mb_layer   = layer + cfg.motion_blur_layer_delta

            # 每次脉冲：副本只在 [pk, end] 段（即峰值 → 平滑回落）出现
            # pk = pulse_w × 0.35, end = pulse_w
            for r in range(n_beats):
                cycle_base = char_show + sync_delay + r * pulse_w
                seg_start = cycle_base + int(pulse_w * 0.35)
                seg_end   = min(sustain_safe_end, cycle_base + pulse_w)
                seg_dur   = seg_end - seg_start
                if seg_dur < 20:
                    continue
                # 副本 alpha：起始即峰值显（淡入很快 ~10%）→ 持续 → 末尾淡出（~30% 时长）
                fade_in_t  = max(1, seg_dur // 10)
                fade_out_t = max(1, seg_dur // 3)
                hold_end   = max(fade_in_t + 1, seg_dur - fade_out_t)
                for y_off, a_int, blur_val in samples:
                    sx = ch.center
                    sy = ch.middle + y_offset + y_off
                    a_tag = f"&H{a_int:02X}&"
                    text = (
                        f"\\an5\\pos({_fmt(sx)},{_fmt(sy)})"
                        f"\\bord{_fmt(max(0.5, base_bord))}\\shad0\\blur{_fmt(blur_val)}"
                        f"\\1c{style_colour}\\3c&H00FFFFFF&\\alpha&HFF&"
                        f"\\t(0,{fade_in_t},\\alpha{a_tag})"
                        f"\\t({hold_end},{seg_dur},\\alpha&HFF&)"
                    )
                    out.append(
                        f"Dialogue: {mb_layer},{_ms_to_ass(seg_start)},{_ms_to_ass(seg_end)},"
                        f"{style},{actor},{ml},{mr},{mv},{eff},"
                        f"{{{text}}}{ch.text}"
                    )

        # ---- 3. Exit: offset + simultaneous fade → red （exit_start 已提前算好）----
        exit_end = exit_start + cfg.offset_dur_ms + cfg.fade_out_ms

        dx_u, dy_u = _DIRECTIONS_8[rng.randrange(8)]
        t_move_end   = cfg.offset_dur_ms
        t_finish_end = cfg.offset_dur_ms + cfg.fade_out_ms
        red_colour   = "&H000000FF&"

        if pieces:
            # 出场也用切片 drawing，从字形中心偏移，保留噪声遮罩
            end_x = gcx + dx_u * cfg.offset_dist
            end_y = gcy + dy_u * cfg.offset_dist
            for drawing, a_int in pieces:
                start_alpha = f"&H{a_int:02X}&"
                if cfg.fade_out_ms <= 0:
                    mid_tag = "&HFF&"
                    tail_t  = ""
                else:
                    mid_a   = min(255, max(a_int, 0xA0))
                    mid_tag = f"&H{mid_a:02X}&"
                    tail_t  = f"\\t({t_move_end},{t_finish_end},\\alpha&HFF&)"
                piece_text = (
                    f"{{\\p1\\an5"
                    f"\\move({_fmt(gcx)},{_fmt(gcy)},{_fmt(end_x)},{_fmt(end_y)},0,{cfg.offset_dur_ms})"
                    f"\\1c{style_colour}\\3c{style_colour}\\alpha{start_alpha}{base_blur_tag}"
                    f"\\t(0,{t_move_end},\\1c{red_colour}\\3c{red_colour}\\alpha{mid_tag})"
                    f"{tail_t}"
                    f"\\bord0\\shad0"
                    f"}}{drawing}"
                )
                out.append(
                    f"Dialogue: {layer},{_ms_to_ass(exit_start)},{_ms_to_ass(exit_end)},"
                    f"{style},{actor},{ml},{mr},{mv},{eff},{piece_text}"
                )
        else:
            ex = _fmt(ch.center + dx_u * cfg.offset_dist)
            ey = _fmt(ch.middle + y_offset + dy_u * cfg.offset_dist)
            if cfg.fade_out_ms <= 0:
                mid_alpha = 255
                tail_t    = ""
            else:
                mid_alpha = 160
                tail_t    = f"\\t({t_move_end},{t_finish_end},\\alpha&HFF&)"

            # 抖动叠加：振幅逐渐变大；alpha 不参与抖动，由外层单一 \t() 平滑淡出处理
            sh_anim = ""
            ex_sh_period = max(20, cfg.exit_shake_period_ms)
            if cfg.exit_shake_enabled and t_move_end >= ex_sh_period * 2:
                n_sh = max(2, t_move_end // ex_sh_period)
                bb_str   = _fmt(base_bord)
                base_blr = _fmt(cfg.sustain_base_blur)
                parts_sh = []
                for s in range(n_sh):
                    t0    = int(t_move_end * (s / n_sh))
                    t_mid = int(t_move_end * ((s + 0.5) / n_sh))
                    t_end_s = int(t_move_end * ((s + 1) / n_sh))
                    progress = s / max(1, n_sh - 1)
                    amp = 1.0 + cfg.exit_shake_amp_growth * progress
                    sh_fscy_v = _fmt(100.0 + cfg.exit_shake_fscy_delta * amp)
                    sh_blur_v = _fmt(font_size * cfg.exit_shake_blur_ratio * amp)
                    sh_bord_v = _fmt(max(base_bord, font_size * cfg.exit_shake_bord_ratio * amp))
                    parts_sh.append(
                        f"\\t({t0},{t_mid},\\fscy{sh_fscy_v}\\blur{sh_blur_v}\\bord{sh_bord_v})"
                        f"\\t({t_mid},{t_end_s},\\fscy100\\blur{base_blr}\\bord{bb_str})"
                    )
                sh_anim = "".join(parts_sh)

            # 颜色快速变红 + alpha 慢慢渐隐（拆开避免颜色被慢插值稀释）
            color_dur = max(50, int(t_move_end * max(0.05, min(1.0, cfg.exit_color_ratio))))
            base_anim = (
                f"\\t(0,{color_dur},\\1c&H0000FF&)"
                f"\\t(0,{t_move_end},\\alpha&H{mid_alpha:02X}&)"
            )
            exit_text = (
                f"{{\\an5\\move({cx},{cy},{ex},{ey},0,{cfg.offset_dur_ms})"
                f"\\alpha&H00&\\1c{style_colour}\\3c{outline_colour}{base_blur_tag}"
                f"{base_anim}{sh_anim}{tail_t}"
                f"{extra_tags}}}{ch.text}"
            )
            out.append(
                f"Dialogue: {layer},{_ms_to_ass(exit_start)},{_ms_to_ass(exit_end)},"
                f"{style},{actor},{ml},{mr},{mv},{eff},{exit_text}"
            )

    return out


# ---------------------------------------------------------------------------
# Main rendering pipeline
# ---------------------------------------------------------------------------

def render_char_fx(
    input_path: str,
    output_path: str,
    *,
    cfg: CharFxConfig | None = None,
    style_filter: str = "",
    keep_original: bool = True,
) -> str:
    cfg = cfg or CharFxConfig()
    rng = random.Random(cfg.random_seed)

    safe_input = _prepare_input(input_path)
    try:
        io = Ass(safe_input, output_path, keep_original=keep_original, extended=True)
        _, styles, lines = io.get_data()

        # 样式定义顺序（[V4+ Styles] 顺序）→ {style_name: index}
        # 在 Aegisub 中靠前定义的样式被视为 "上方行"（碰撞时被推上去）
        try:
            style_order = {name: i for i, name in enumerate(styles.keys())}
        except AttributeError:
            style_order = {}

        collision_offsets = _compute_collision_offsets(lines, style_order)
        generated: list[str] = []

        for line in lines:
            if line.comment:
                continue
            if style_filter and line.style != style_filter:
                continue
            y_off = collision_offsets.get(line.i, 0.0)
            generated.extend(_build_char_events(line, cfg, rng, y_offset=y_off))

        for evt in generated:
            io._output.append(evt + "\n")
            io._plines += 1

        io.save()
    finally:
        if safe_input != input_path:
            try:
                os.unlink(safe_input)
            except OSError:
                pass

    return output_path


# ---------------------------------------------------------------------------
# CLI — 所有参数均带中文 help
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    # 所有默认值来自 CharFxConfig，保证唯一真源
    d = CharFxConfig()
    def ms_alpha_to_pct(a: int) -> float:
        return round(a / 255 * 100, 1)
    p = argparse.ArgumentParser(
        description="ASS逐字KTV特效：白色淡入 → 卡拉OK持续脉冲+噪声遮罩 → 偏移变红渐隐"
    )
    p.add_argument("input", help="输入ASS文件路径")
    p.add_argument("-o", "--output", default=None,
                   help="输出ASS文件路径（默认: <输入名>_charfx.ass）")

    g_entry = p.add_argument_group("入场特效（Entry）")
    g_entry.add_argument("--char-delay", type=int, default=d.char_delay_ms, metavar="MS",
                         help=f"逐字入场间隔毫秒，0=全部同时入场（默认{d.char_delay_ms}）")
    g_entry.add_argument("--fade-in",    type=int, default=d.fade_in_ms, metavar="MS",
                         help=f"每字白色淡入时长毫秒（默认{d.fade_in_ms}）")

    g_sus = p.add_argument_group("持续脉冲（Sustain）")
    g_sus.add_argument("--sustain-peak-scale", type=float, default=d.sustain_peak_scale, metavar="PCT",
                       help=f"脉冲峰值时字体放大百分比（默认{d.sustain_peak_scale}）")
    g_sus.add_argument("--sustain-peak-bord",  type=float, default=d.sustain_peak_bord,  metavar="PX",
                       help=f"脉冲峰值时描边粗细像素（默认{d.sustain_peak_bord}）")
    g_sus.add_argument("--sustain-peak-blur",  type=float, default=d.sustain_peak_blur,  metavar="PX",
                       help=f"脉冲峰值时模糊半径像素（默认{d.sustain_peak_blur}）")
    g_sus.add_argument("--sustain-base-blur",  type=float, default=d.sustain_base_blur,  metavar="PX",
                       help=f"非脉冲时常驻模糊半径像素（默认{d.sustain_base_blur}）")
    g_sus.add_argument("--sustain-alpha-dip",  type=float, default=ms_alpha_to_pct(d.sustain_alpha_dip), metavar="PCT",
                       help=f"脉冲时短暂渐隐的透明度百分比，0=不透明 100=全透明（默认{ms_alpha_to_pct(d.sustain_alpha_dip)}）")
    g_sus.add_argument("--sustain-speed",      type=float, default=d.sustain_speed,      metavar="X",
                       help=f"脉冲速度倍率，>1更快 <1更慢，剩余时间自动按单次时长重复（默认{d.sustain_speed}）")
    g_sus.add_argument("--sustain-min-ms",     type=int,   default=d.sustain_min_ms,     metavar="MS",
                       help=f"K时间短于此值则跳过脉冲动画（默认{d.sustain_min_ms}）")

    g_mask = p.add_argument_group("噪声遮罩（Noise Mask）")
    g_mask.add_argument("--no-noise-mask", dest="noise_mask_enabled",
                        action="store_false",
                        help="关闭噪声遮罩层")
    g_mask.add_argument("--noise-mask-alpha", type=int, default=int(ms_alpha_to_pct(d.noise_mask_alpha)), metavar="PCT",
                        help=f"遮罩透明度百分比，0=字体完整 100=完全挖空（默认{int(ms_alpha_to_pct(d.noise_mask_alpha))}）")
    g_mask.add_argument("--noise-mask-scale", type=float, default=d.noise_mask_scale, metavar="F",
                        help=f"噪声UV缩放，越大越密（默认{d.noise_mask_scale}）")
    g_mask.add_argument("--noise-mask-steps", type=int, default=d.noise_mask_steps, metavar="N",
                        help=f"噪声灰度层数，越多越细腻越慢（默认{d.noise_mask_steps}）")

    g_exit = p.add_argument_group("出场特效（Exit）")
    g_exit.add_argument("--offset-dist",  type=float, default=d.offset_dist,         metavar="PX",
                        help=f"出场偏移距离像素（默认{d.offset_dist}）")
    g_exit.add_argument("--offset-dur",   type=int,   default=d.offset_dur_ms,       metavar="MS",
                        help=f"出场偏移移动时长毫秒，与渐隐同时进行（默认{d.offset_dur_ms}）")
    g_exit.add_argument("--fade-out",     type=int,   default=d.fade_out_ms,         metavar="MS",
                        help=f"偏移结束后继续淡出时长毫秒（默认{d.fade_out_ms}）")
    g_exit.add_argument("--exit-stagger", type=int,   default=d.exit_stagger_max_ms, metavar="MS",
                        help=f"每字出场随机延迟上限毫秒（默认{d.exit_stagger_max_ms}）")

    p.add_argument("--inject-tags", default="", metavar="TAGS",
                   help=r"强制注入到每行的额外ASS标签，如 \fs102\bord1")
    p.add_argument("--style", default="", metavar="NAME",
                   help="只处理指定样式名的行，留空=处理所有")
    p.add_argument("--no-keep-original", dest="keep_original",
                   action="store_false",
                   help="不保留原始字幕行（默认保留为注释）")
    p.add_argument("--seed", type=int, default=12345,
                   help="出场方向随机种子（默认12345）")
    return p


def main(argv: Sequence[str] | None = None) -> None:
    for sname in ("stdout", "stderr"):
        s = getattr(sys, sname, None)
        rc = getattr(s, "reconfigure", None)
        if callable(rc):
            try: rc(encoding="utf-8")
            except Exception: pass

    args = _build_parser().parse_args(argv)
    input_path = os.path.abspath(args.input)
    output_path = (os.path.abspath(args.output) if args.output
                   else os.path.splitext(input_path)[0] + "_charfx.ass")

    cfg = CharFxConfig(
        char_delay_ms=args.char_delay,
        fade_in_ms=args.fade_in,
        sustain_peak_scale=args.sustain_peak_scale,
        sustain_peak_bord=args.sustain_peak_bord,
        sustain_peak_blur=args.sustain_peak_blur,
        sustain_base_blur=args.sustain_base_blur,
        sustain_alpha_dip=int(round(
            max(0.0, min(100.0, args.sustain_alpha_dip)) / 100.0 * 255)),
        sustain_speed=args.sustain_speed,
        sustain_min_ms=args.sustain_min_ms,
        noise_mask_enabled=args.noise_mask_enabled,
        noise_mask_alpha=int(round(
            max(0.0, min(100.0, args.noise_mask_alpha)) / 100.0 * 255)),
        noise_mask_scale=args.noise_mask_scale,
        noise_mask_steps=args.noise_mask_steps,
        offset_dist=args.offset_dist,
        offset_dur_ms=args.offset_dur,
        fade_out_ms=args.fade_out,
        exit_stagger_max_ms=args.exit_stagger,
        inject_tags=args.inject_tags,
        random_seed=args.seed,
    )

    result = render_char_fx(
        input_path, output_path,
        cfg=cfg,
        style_filter=args.style,
        keep_original=args.keep_original,
    )
    print(f"Written: {result}")


if __name__ == "__main__":
    main()
