# Redline

`redline.py` renders a karaoke line effect built from two cooperating layers:

- `melt`: the external red underline, knot, sparks, and butterfly launches
- `word`: the text-entry layer driven by stroke-order assets

The default mode is `combined`, which runs both together:

- `word` handles how the text enters
- `melt` handles the non-text redline effect around the text

## Layout

Main files:

- [redline.py](./redline.py): primary render pipeline and CLI entry point
- [README.md](./README.md): usage and tuning notes
- [butterfly/output/butterfly.10frames.0.2s.ass](./butterfly/output/butterfly.10frames.0.2s.ass): butterfly vector frames

Word-effect sidecar modules:

- [word/word_fx_adapter.py](./word/word_fx_adapter.py): bridge used by `redline.py`
- [word/stroke_fx](./word/stroke_fx): reusable stroke-order asset, timing, and render modules
- [word/stroke_word_test.py](./word/stroke_word_test.py): local test harness for the word-entry pipeline

## Effect Modes

`redline.py` supports three render modes:

- `combined`: run `word` and `melt` together
- `melt`: run only the underline / spark / butterfly layer
- `word`: run only the text-entry layer

Example:

```powershell
python effects\redline\redline.py `
  --input in.ass `
  --output output.ass `
  --effect-mode combined
```

## Input Expectations

This effect is designed for karaoke-style ASS input where syllable timing already exists.

- lines should contain `\k`, `\kf`, or equivalent karaoke timing
- `melt` relies on `line.syls`, so non-karaoke dialogue will not produce the intended underline behavior
- `word` can render character entry from line text, but `combined` mode is intended for syllable-timed karaoke input
- only non-comment bottom-aligned lines are selected by default
- `--extended true` is the expected mode because geometry comes from PyonFX extended parsing

If a line has no usable syllables, the `melt` layer contributes nothing for that line.

## Quick Start

From the repository root:

```powershell
python effects\redline\redline.py --input in.ass --output output.ass
```

Default behavior:

- `effect_mode=combined`
- generated vector style name is `p`
- original dialogue lines are kept as comments
- multiprocessing is enabled when enough target lines are present

## CLI

Base options:

- `--input`: input ASS path
- `--output`: output ASS path
- `--style-name`: generated effect style name, default `p`
- `--keep-original`: whether original dialogue lines are kept, default `true`
- `--extended`: whether `Ass(...)` should compute extended geometry, default `true`
- `--effect-mode`: `combined`, `melt`, or `word`
- `--word-root`: root path for the external word-effect assets, default `effects/redline/word`

Every field in `MeltConfig` is also exposed as a CLI override. Example:

```powershell
python effects\redline\redline.py `
  --input in.ass `
  --output output.ass `
  --effect-mode combined `
  --line-lead-in-ms 640 `
  --line-width-px 5.0 `
  --curve-arc-px 22 `
  --butterfly-scale 1.15 `
  --enable-multiprocessing false
```

## Render Model

At a high level, `render_spike(...)` does the following:

1. Load ASS lines and add the generated vector style.
2. Select target lines with `_select_target_lines(...)`.
3. Apply bottom-line collision offsets.
4. If `effect_mode` includes `word`, render word-entry events through `word_fx_adapter.py`.
5. If `effect_mode` includes `melt`, render underline / spark / butterfly events through the main pipeline.
6. Write generated ASS events back to the output file.

Per line, `melt_line(...)`:

- builds the main underline stroke from the full line span
- runs a rapid left-to-right lead-in for the stroke
- wipes the ribbon through sampled centerline slices
- attaches spark slashes near the wipe front
- releases butterflies from syllables and the tail knot

## Main Tuning Areas

`MeltConfig` is large, but most practical tuning falls into these groups:

- timing:
  `line_lead_in_ms`, `line_fade_in_ms`, `line_highlight_ms`, `line_long_hold_tail_ms`
- stroke shape:
  `line_width_px`, `curve_arc_px`, `curve_wave_amp_px`, `curve_wave_cycles`, `sample_density_px`
- surface motion:
  `static_amp_px`, `flow_amp_px`, `boil_amp_px`
- sparks:
  `spark_count_min`, `spark_count_max`, `spark_len_min_px`, `spark_len_max_px`, `spark_drift_px`
- butterfly behavior:
  `butterfly_duration_ms`, `butterfly_scale`, `butterfly_arc_px`, `butterfly_min_syllable_gap`, `butterfly_max_syllable_gap`
- performance:
  `enable_multiprocessing`, `multiprocessing_min_lines`, `max_workers`

Word-entry timing and asset behavior are configured separately in [word/stroke_fx/config.py](./word/stroke_fx/config.py).

## Word-Effect Notes

The `word` layer is intentionally external to the main `melt` implementation.

- `redline.py` imports a bridge from `word/word_fx_adapter.py`
- reusable logic stays in `word/stroke_fx`
- test-harness outputs such as `test_input.ass` / `test_output.ass` belong in local effect development, not the main CLI path

This separation keeps the main script focused on orchestration while the stroke-order text-entry pipeline remains modular.

## Butterfly Asset

Butterfly frames are loaded relative to `redline.py`, not the current working directory.

Expected asset path:

```text
effects/redline/butterfly/output/butterfly.10frames.0.2s.ass
```

If the file is missing or does not contain the expected 10 drawing frames, rendering will fail.

## Notes

- The generated effect is vector-heavy and can produce a large number of ASS events.
- Multiprocessing is useful for larger scripts but can be disabled for iteration.
- This README documents the repository version under `effects/redline/`; local experiments should be documented separately if they diverge.
