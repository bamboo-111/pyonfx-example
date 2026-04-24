# Redline

`redline.py` renders a syllable-driven red underline effect for ASS karaoke lines.

The effect is built from four parts:

- a procedural red ribbon stroke that wipes across the line
- a right-side knot tail attached to the main stroke
- short spark slashes that follow the wipe front
- butterfly launches emitted from syllable tails and long holds

The implementation is self-contained in [redline.py](./redline.py), with butterfly vector frames loaded from [butterfly/output/butterfly.10frames.0.2s.ass](./butterfly/output/butterfly.10frames.0.2s.ass).

## Input Expectations

This script is designed for karaoke-style ASS input where lines already contain syllable timing.

- lines should have `\k`, `\kf`, or equivalent karaoke timing so `line.syls` is meaningful
- only non-comment bottom-aligned lines are rendered by default
- the effect follows syllable geometry computed by PyonFX extended parsing, so `--extended true` is the normal mode

If the input line has no usable syllables, `redline.py` emits nothing for that line.

## Quick Start

From the repository root:

```powershell
python effects\redline\redline.py --input in.ass --output output.ass
```

By default the script:

- parses the ASS file with `extended=True`
- writes generated vector events in style `p`
- keeps the original dialogue lines as comments
- enables multiprocessing when enough target lines are present

## CLI

Basic options:

- `--input`: input ASS path
- `--output`: output ASS path
- `--style-name`: generated effect style name, default `p`
- `--keep-original`: whether to keep original dialogue lines, default `true`
- `--extended`: whether `Ass(...)` should compute extended geometry, default `true`

Every field in `MeltConfig` is also exposed as a CLI override. For example:

```powershell
python effects\redline\redline.py `
  --input in.ass `
  --output output.ass `
  --line-width-px 5.0 `
  --curve-arc-px 22 `
  --butterfly-scale 1.15 `
  --enable-multiprocessing false
```

## Render Model

At a high level, `render_spike(...)` works like this:

1. Load ASS lines and add the generated vector style.
2. Select renderable target lines with `_select_target_lines(...)`.
3. Apply bottom-line collision offsets so overlapping subtitles do not stack on top of each other.
4. Render each line either locally or through multiprocessing workers.
5. Write generated ASS vector events back to the output file.

Per line, `melt_line(...)`:

- builds the main underline stroke from the full line span
- wipes the ribbon through sampled centerline slices
- attaches spark slashes to the wipe front
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

## Butterfly Asset

Butterfly frames are loaded relative to `redline.py`, not the process working directory.

Expected asset path:

```text
effects/redline/butterfly/output/butterfly.10frames.0.2s.ass
```

If the file is missing or does not contain the expected 10 drawing frames, rendering will fail.

## Notes

- The generated effect is vector-heavy and can produce a large number of ASS events.
- Multiprocessing is helpful for larger scripts, but for quick iteration it can be useful to disable it.
- This README documents the repository version under `effects/redline/`. Experimental local variants should be documented separately if they diverge.
