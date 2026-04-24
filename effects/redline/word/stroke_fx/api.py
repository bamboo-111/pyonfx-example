from __future__ import annotations

from .assets import build_word_assets, build_word_assets_for_lines, ensure_dirs
from .config import WordFxConfig, WordFxPaths, WordFxRenderOptions, WordFxTestContext
from .diagnostics import write_debug_json, write_static_glyph_diagnostic
from .input import parse_selected_japanese_lines, write_test_input_ass
from .integration import (
    WordFxSession,
    build_word_fx_session,
    render_word_effect_with_session,
    warm_word_assets_from_ass,
)
from .render import render_word_effect, render_word_effect_for_lines


def run_test_harness(ctx: WordFxTestContext) -> dict[str, object]:
    ensure_dirs(ctx.paths)
    selected_lines = parse_selected_japanese_lines(ctx)
    write_test_input_ass(ctx, selected_lines)
    assets = build_word_assets_for_lines(selected_lines, paths=ctx.paths, config=ctx.config)
    line_debug = render_word_effect_for_lines(
        str(ctx.paths.test_input_path),
        str(ctx.paths.test_output_path),
        assets,
        config=ctx.config,
    )
    write_debug_json(ctx.paths, ctx.config, selected_lines, assets, line_debug)
    if ctx.config.diagnostic_char in assets:
        write_static_glyph_diagnostic(
            ctx.paths,
            ctx.config,
            assets[ctx.config.diagnostic_char],
            ctx.config.diagnostic_char,
        )
    return {
        "lines": selected_lines,
        "assets": assets,
        "line_debug": line_debug,
    }


__all__ = [
    "WordFxConfig",
    "WordFxPaths",
    "WordFxRenderOptions",
    "WordFxSession",
    "WordFxTestContext",
    "build_word_assets",
    "build_word_assets_for_lines",
    "build_word_fx_session",
    "parse_selected_japanese_lines",
    "render_word_effect",
    "render_word_effect_for_lines",
    "render_word_effect_with_session",
    "run_test_harness",
    "warm_word_assets_from_ass",
    "write_debug_json",
    "write_static_glyph_diagnostic",
    "write_test_input_ass",
]
