from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WordFxPaths:
    root: Path
    lyric_path: Path
    test_input_path: Path
    test_output_path: Path
    test_debug_path: Path
    static_diagnostic_path: Path
    cache_dir: Path
    download_dir: Path
    glyph_asset_dir: Path
    temp_svg_dir: Path
    svg2ass_exe: Path

    @classmethod
    def for_word_root(cls, root: Path) -> "WordFxPaths":
        cache_dir = root / "cache"
        return cls(
            root=root,
            lyric_path=root / "19.蝶に結いた赤い糸.txt",
            test_input_path=root / "test_input.ass",
            test_output_path=root / "test_output.ass",
            test_debug_path=root / "test_debug.json",
            static_diagnostic_path=root / "diagnostic_hou_static.ass",
            cache_dir=cache_dir,
            download_dir=cache_dir / "downloads",
            glyph_asset_dir=cache_dir / "glyph_assets",
            temp_svg_dir=cache_dir / "temp_svg",
            svg2ass_exe=root.parent.parent / "svg2ass" / "svg2ass.exe",
        )


@dataclass(frozen=True, slots=True)
class WordFxConfig:
    play_res_x: int = 1920
    play_res_y: int = 1080
    font_name: str = "Heisei Maru Gothic Std W4"
    font_size: int = 60
    test_input_start_offset_ms: int = 3000
    line_entry_lead_in_ms: int = 420
    line_entry_start_advance_ms: int = 900
    draw_p_scale: int = 1
    min_stroke_ms: int = 40
    assembly_hold_ms: int = 100
    highlight_ms: int = 360
    whole_char_move_ms: int = 180
    text_fade_in_ms: int = 160
    text_min_visible_hold_ms: int = 180
    text_fade_offset_ms: int = 80
    text_fade_ms: int = 180
    request_timeout: int = 30
    asset_cache_version: int = 2
    line_stroke_timing_scale: tuple[float, ...] = (1.75, 1.28, 1.0)
    line_travel_distance_scale: tuple[float, ...] = (0.58, 0.76, 0.88)
    line_stroke_overlap_ratio: tuple[float, ...] = (0.42, 0.34, 0.24)
    char_entry_step_ms: int = 100
    target_lines: tuple[str, ...] = (
        "君と出逢えた始まり",
        "蝶に結いた赤い糸",
        "気のせいじゃないと　気づいていたんだ",
    )
    line_durations_ms: tuple[int, ...] = (4200, 4200, 5200)
    animcjk_ja_url: str = (
        "https://raw.githubusercontent.com/parsimonhi/animCJK/master/svgsJa/{code}.svg"
    )
    animcjk_ja_kana_url: str = (
        "https://raw.githubusercontent.com/parsimonhi/animCJK/master/svgsJaKana/{code}.svg"
    )
    kanjivg_url: str = (
        "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji/{code}.svg"
    )
    diagnostic_char: str = "逢"


@dataclass(frozen=True, slots=True)
class WordFxRenderOptions:
    layer_base: int = 0
    stroke_layer_offset: int = 10
    highlight_layer_offset: int = 20
    final_layer_offset: int = 30


@dataclass(frozen=True, slots=True)
class WordFxTestContext:
    paths: WordFxPaths
    config: WordFxConfig = field(default_factory=WordFxConfig)
