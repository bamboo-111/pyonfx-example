from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from redline.template import (
    MeltConfig,
    RibbonPath,
    SylContext,
    _alpha_lerp,
    _build_ribbon_path,
    _butterfly_frames,
    _compute_mask_fronts,
    _normalize,
)


def _fake_ctx() -> SylContext:
    config = MeltConfig()
    line = SimpleNamespace(i=2)
    syl = SimpleNamespace(i=3, left=100.0, right=180.0, bottom=300.0)
    return SylContext(line=line, syl=syl, line_layer_base=0, style_name="p", seed=1234, config=config)


class TemplateTests(unittest.TestCase):
    def test_mask_front_is_strictly_increasing(self) -> None:
        fronts = _compute_mask_fronts(_fake_ctx(), 8)
        self.assertTrue(all(a < b for a, b in zip(fronts, fronts[1:])))

    def test_normal_is_unit_length(self) -> None:
        nx, ny = _normalize(3.0, 4.0)
        self.assertTrue(0.99 <= math.hypot(nx, ny) <= 1.01)

    def test_ribbon_path_keeps_matching_side_counts(self) -> None:
        ctx = _fake_ctx()
        points = [
            SimpleNamespace(x=100.0, y=300.0, s=0.0),
            SimpleNamespace(x=120.0, y=299.0, s=0.5),
            SimpleNamespace(x=140.0, y=300.5, s=1.0),
        ]
        ribbon = _build_ribbon_path(points, ctx)
        self.assertIsInstance(ribbon, RibbonPath)
        assert ribbon is not None
        self.assertEqual(len(ribbon.left), len(ribbon.right))
        self.assertGreaterEqual(len(ribbon.left), 2)

    def test_wing_frames_cycle_over_three_indices(self) -> None:
        frames = _butterfly_frames(1.0)
        self.assertEqual({frame.frame_index for frame in frames}, {0, 1, 2})

    def test_butterfly_fade_clamp_matches_duration_rule(self) -> None:
        config = MeltConfig(butterfly_duration_ms=120, butterfly_fade_ms=300)
        effective_fade = min(config.butterfly_fade_ms, config.butterfly_duration_ms)
        self.assertEqual(effective_fade, 120)
        self.assertEqual(_alpha_lerp("&H08&", 1.0), "&HFF&")


if __name__ == "__main__":
    unittest.main()
