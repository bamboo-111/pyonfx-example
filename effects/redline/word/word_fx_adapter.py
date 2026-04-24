from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyonfx import Ass, Line

from .stroke_fx import (
    WordFxConfig,
    WordFxPaths,
    WordFxRenderOptions,
    WordFxSession,
    build_word_fx_session,
)


DEFAULT_LAYERS_PER_LINE = 5


def default_word_fx_selector(line: Line) -> bool:
    return not line.comment and line.styleref.alignment in (1, 2, 3)


def select_word_fx_target_lines(
    lines: list[Line],
    selector: Callable[[Line], bool] | None = None,
) -> list[tuple[int, Line]]:
    selector = selector or default_word_fx_selector
    result: list[tuple[int, Line]] = []
    line_index = 0
    for line in lines:
        if not selector(line):
            continue
        result.append((line_index, line))
        line_index += 1
    return result


def default_word_fx_layer_base(line: Line, line_index: int, *, layers_per_line: int = DEFAULT_LAYERS_PER_LINE) -> int:
    return max(int(getattr(line, "layer", 0)) + 10, 10) + line_index * layers_per_line


@dataclass(slots=True)
class WordFxBridge:
    session: WordFxSession
    layers_per_line: int = DEFAULT_LAYERS_PER_LINE

    def warm_for_target_lines(self, target_lines: list[tuple[int, Line]]) -> dict[str, Any]:
        lines_only = [line for _, line in target_lines]
        return self.session.warm_assets_for_ass_lines(lines_only)

    def render_target_lines(
        self,
        io: Ass,
        target_lines: list[tuple[int, Line]],
        *,
        layer_builder: Callable[[Line, int], int] | None = None,
        line_preparer: Callable[[Line, int], Line] | None = None,
    ) -> list[dict[str, Any]]:
        self.warm_for_target_lines(target_lines)
        layer_builder = layer_builder or (
            lambda line, line_index: default_word_fx_layer_base(
                line,
                line_index,
                layers_per_line=self.layers_per_line,
            )
        )
        line_preparer = line_preparer or (lambda line, _line_index: line)

        debug_rows: list[dict[str, Any]] = []
        for line_index, line in target_lines:
            prepared_line = line_preparer(line, line_index)
            debug_rows.append(
                self.session.render_line(
                    io,
                    prepared_line,
                    line_index,
                    options=WordFxRenderOptions(layer_base=layer_builder(prepared_line, line_index)),
                )
            )
        return debug_rows


def shift_word_fx_line(line: Line, y_offset: float) -> Line:
    if y_offset == 0.0:
        return line

    shifted = line.copy()
    for attr in ("top", "bottom", "middle"):
        if hasattr(shifted, attr):
            setattr(shifted, attr, getattr(shifted, attr) + y_offset)

    for char in getattr(shifted, "chars", []):
        for attr in ("top", "bottom", "middle", "y"):
            if hasattr(char, attr):
                setattr(char, attr, getattr(char, attr) + y_offset)

    return shifted


def create_word_fx_bridge(
    *,
    word_root: str | Path | None = None,
    paths: WordFxPaths | None = None,
    config: WordFxConfig | None = None,
    layers_per_line: int = DEFAULT_LAYERS_PER_LINE,
) -> WordFxBridge:
    if paths is None:
        resolved_root = Path(word_root) if word_root is not None else Path(__file__).resolve().parent
        paths = WordFxPaths.for_word_root(resolved_root)
    session = build_word_fx_session(paths=paths, config=config or WordFxConfig())
    return WordFxBridge(session=session, layers_per_line=layers_per_line)


def render_word_fx_from_ass(
    io: Ass,
    lines: list[Line],
    *,
    selector: Callable[[Line], bool] | None = None,
    bridge: WordFxBridge | None = None,
    word_root: str | Path | None = None,
    config: WordFxConfig | None = None,
) -> list[dict[str, Any]]:
    bridge = bridge or create_word_fx_bridge(word_root=word_root, config=config)
    target_lines = select_word_fx_target_lines(lines, selector=selector)
    return bridge.render_target_lines(io, target_lines)


__all__ = [
    "DEFAULT_LAYERS_PER_LINE",
    "WordFxBridge",
    "create_word_fx_bridge",
    "default_word_fx_layer_base",
    "default_word_fx_selector",
    "render_word_fx_from_ass",
    "select_word_fx_target_lines",
    "shift_word_fx_line",
]
