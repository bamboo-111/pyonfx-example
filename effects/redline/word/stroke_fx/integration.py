from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyonfx import Ass, Utils

from .assets import build_word_assets, build_word_assets_for_lines, ensure_dirs
from .config import WordFxConfig, WordFxPaths, WordFxRenderOptions
from .models import GlyphAsset
from .render import render_word_effect
from .utils import is_drawable_char


def collect_drawable_chars(text: str) -> set[str]:
    return {char for char in text if is_drawable_char(char)}


def collect_drawable_chars_from_lines(lines: list[Any]) -> set[str]:
    chars: set[str] = set()
    for line in lines:
        chars.update(collect_drawable_chars(getattr(line, "text", "")))
    return chars


@dataclass(slots=True)
class WordFxSession:
    paths: WordFxPaths
    config: WordFxConfig = field(default_factory=WordFxConfig)
    assets: dict[str, GlyphAsset] = field(default_factory=dict)

    def ensure_ready(self) -> None:
        ensure_dirs(self.paths)

    def warm_assets_for_chars(self, chars: set[str] | list[str]) -> dict[str, GlyphAsset]:
        self.ensure_ready()
        warmed = build_word_assets(chars, paths=self.paths, config=self.config)
        self.assets.update(warmed)
        return warmed

    def warm_assets_for_texts(self, texts: list[str]) -> dict[str, GlyphAsset]:
        self.ensure_ready()
        warmed = build_word_assets_for_lines(texts, paths=self.paths, config=self.config)
        self.assets.update(warmed)
        return warmed

    def warm_assets_for_ass_lines(self, lines: list[Any]) -> dict[str, GlyphAsset]:
        return self.warm_assets_for_chars(collect_drawable_chars_from_lines(lines))

    def get_asset(self, char: str) -> GlyphAsset:
        if char not in self.assets:
            self.warm_assets_for_chars({char})
        return self.assets[char]

    def ensure_assets_for_line(self, line: Any) -> dict[str, GlyphAsset]:
        chars = collect_drawable_chars(getattr(line, "text", ""))
        missing = {char for char in chars if char not in self.assets}
        if missing:
            self.warm_assets_for_chars(missing)
        return {char: self.assets[char] for char in chars}

    def render_line(
        self,
        io: Ass,
        line: Any,
        line_index: int,
        *,
        options: WordFxRenderOptions | None = None,
    ) -> dict[str, Any]:
        self.ensure_assets_for_line(line)
        return render_word_effect(
            io,
            line,
            line_index,
            self.assets,
            config=self.config,
            options=options,
        )

    def render_lines(
        self,
        io: Ass,
        lines: list[Any],
        *,
        layer_base: int = 0,
        layer_step: int = 0,
    ) -> list[dict[str, Any]]:
        if lines:
            self.warm_assets_for_ass_lines(lines)

        debug_rows: list[dict[str, Any]] = []
        for line_index, line in enumerate(lines):
            options = WordFxRenderOptions(layer_base=layer_base + line_index * layer_step)
            debug_rows.append(self.render_line(io, line, line_index, options=options))
        return debug_rows


def build_word_fx_session(
    *,
    paths: WordFxPaths,
    config: WordFxConfig | None = None,
) -> WordFxSession:
    return WordFxSession(paths=paths, config=config or WordFxConfig())


def warm_word_assets_from_ass(
    input_path: str,
    *,
    paths: WordFxPaths,
    config: WordFxConfig | None = None,
    extended: bool = True,
) -> WordFxSession:
    session = build_word_fx_session(paths=paths, config=config)
    io = Ass(input_path, input_path, keep_original=True, extended=extended)
    _, _, lines = io.get_data()
    session.warm_assets_for_ass_lines(lines)
    return session


def render_word_effect_with_session(
    io: Ass,
    line: Any,
    line_index: int,
    *,
    session: WordFxSession,
    options: WordFxRenderOptions | None = None,
) -> dict[str, Any]:
    return session.render_line(io, line, line_index, options=options)


def iter_non_empty_chars(line: Any) -> list[Any]:
    return list(Utils.all_non_empty(line.chars, progress_bar=False))
