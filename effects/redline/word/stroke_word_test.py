from __future__ import annotations

from pathlib import Path

from stroke_fx import WordFxPaths, WordFxTestContext, run_test_harness


ROOT = Path(__file__).resolve().parent


def main() -> None:
    ctx = WordFxTestContext(paths=WordFxPaths.for_word_root(ROOT))
    result = run_test_harness(ctx)
    print(f"Wrote {ctx.paths.test_input_path}")
    print(f"Wrote {ctx.paths.test_output_path}")
    print(f"Wrote {ctx.paths.test_debug_path}")
    if ctx.config.diagnostic_char in result["assets"]:
        print(f"Wrote {ctx.paths.static_diagnostic_path}")


if __name__ == "__main__":
    main()
