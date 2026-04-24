from __future__ import annotations

from .config import WordFxTestContext
from .utils import ass_timestamp, is_drawable_char, strip_tags


DEFAULT_SYL_K = 25


def to_char_karaoke_text(text: str, syl_k: int = DEFAULT_SYL_K) -> str:
    parts: list[str] = []
    for char in text:
        if is_drawable_char(char):
            parts.append(f"{{\\k{syl_k}}}{char}")
        else:
            parts.append(char)
    return "".join(parts)


def parse_selected_japanese_lines(ctx: WordFxTestContext) -> list[str]:
    text = ctx.paths.lyric_path.read_text(encoding="utf-8")
    selected: list[str] = []
    target_line_set = set(ctx.config.target_lines)
    for raw_line in text.splitlines():
        if "\\N" not in raw_line:
            continue
        _, jp_part = raw_line.split("\\N", 1)
        jp_text = strip_tags(jp_part).strip()
        if jp_text in target_line_set and jp_text not in selected:
            selected.append(jp_text)

    missing = [line for line in ctx.config.target_lines if line not in selected]
    if missing:
        raise RuntimeError(f"Missing target lyric lines: {missing}")
    return selected


def write_test_input_ass(ctx: WordFxTestContext, lines: list[str]) -> None:
    config = ctx.config
    styles = (
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{config.font_name},{config.font_size},&H00FFFFFF,&H000000FF,&H002A1A32,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,60,1\n"
    )
    events = ["[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    line_start = 0
    for text, duration in zip(lines, config.line_durations_ms, strict=True):
        start = ass_timestamp(line_start)
        end = ass_timestamp(line_start + duration)
        karaoke_text = to_char_karaoke_text(text)
        events.append(f"Dialogue: 0,{start},{end},Default,,0000,0000,0000,,{karaoke_text}")
        line_start += duration + 800

    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        f"PlayResX: {config.play_res_x}\n"
        f"PlayResY: {config.play_res_y}\n\n"
        f"{styles}\n"
        + "\n".join(events)
        + "\n"
    )
    ctx.paths.test_input_path.write_text(content, encoding="utf-8")
