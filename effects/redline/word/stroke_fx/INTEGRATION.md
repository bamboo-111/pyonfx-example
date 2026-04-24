# stroke_fx integration notes

## External integration with redline.py

Keep `stroke_fx` as an external package under `redline/word/`.
Do not copy its internals into `redline.py`.

Recommended integration shape:

1. Build one session per render pass.
2. Warm assets for the target lines before event emission.
3. For each target line, call `session.render_line(io, line, line_index, options=...)`.
4. Keep test harness and diagnostics in `word/`, not in `redline.py`.
5. If you want a thinner bridge, use `word/word_fx_adapter.py` instead of importing low-level modules directly.

## Minimal adapter example

```python
from word.word_fx_adapter import create_word_fx_bridge, select_word_fx_target_lines

bridge = create_word_fx_bridge()
target_lines = select_word_fx_target_lines(lines)
debug_rows = bridge.render_target_lines(io, target_lines)
```

## What should stay outside redline.py

- SVG download and cache management
- glyph asset cache format and compatibility handling
- svg2ass conversion details
- test input generation
- debug JSON output
- static diagnostic ASS output

## What redline.py should inject

- current `Ass` instance
- target `line`
- `line_index`
- layer base / layer policy
- optional `WordFxConfig` overrides

## Future extension points

- line selection policy
- style/theme variants
- shared diagnostics collector
- prebuild asset warm-up stage for batch rendering
