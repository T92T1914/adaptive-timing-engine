"""Keep the new lifecycle figure tied to its public saved evidence."""

import json
import re
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET

from tools import render_revised_lifecycle as figure


NS = {"s": "http://www.w3.org/2000/svg", "dc": "http://purl.org/dc/elements/1.1/"}


class RevisedLifecycleFigureTests(unittest.TestCase):
    def test_saved_schedule_and_narrow_text_size(self):
        record = figure.check_outputs()
        evidence = record["evidence"]
        self.assertFalse(evidence["experiment_rerun"])
        self.assertEqual(evidence["metrics"]["offered"], 3)
        self.assertEqual(evidence["metrics"]["physical_completed"], 2)
        self.assertEqual(evidence["metrics"]["stale"], 1)
        self.assertEqual(evidence["current"]["revision"], 3)
        self.assertEqual(evidence["current"]["total"], 142336)
        self.assertEqual(evidence["terminal_cleanup"]["outstanding"], 0)
        self.assertIn("Its unsubmitted work never runs.", evidence["text_equivalent"])
        narrow, wide = record["layout"]["narrow"], record["layout"]["wide"]
        self.assertEqual((narrow["width"], wide["width"]), (960, 1800))
        self.assertGreaterEqual(narrow["minimum_font_points"], 33)
        self.assertGreaterEqual(wide["minimum_font_points"], 30)
        self.assertEqual(narrow["cards"], 5)
        self.assertEqual(
            sorted(record["typography"]["files"]), ["Bold", "Regular", "SemiBold"]
        )
        self.assertFalse(record["retained_alternates"]["rebuilt"])

    def test_svg_editions_preserve_order_geometry_and_text_evidence(self):
        record = figure.check_outputs()
        allowed = set(json.loads(
            (figure.ROOT / figure.TOKENS_PATH).read_text())["colors"].values())
        for name in ("docs/revised-lifecycle-obscur.svg",
                     "docs/revised-lifecycle-obscur-wide.svg"):
            text = (figure.ROOT / name).read_text()
            root = ET.fromstring(text)
            self.assertEqual(json.loads(root.find(".//dc:description", NS).text),
                             record["evidence"])
            for key, _, _, _ in figure.STAGES:
                self.assertIsNotNone(
                    root.find(f".//s:g[@id='event-{key}']/s:path", NS))
            for face in ("Regular", "SemiBold", "Bold"):
                self.assertIn(f'id="Inter-{face}-', text)
            self.assertLessEqual(set(re.findall(r"#[0-9a-fA-F]{6}", text)), allowed)
            self.assertNotIn("Italic", text)
            self.assertNotIn("<text", text)

    def test_check_needs_no_authoring_or_product_imports(self):
        probe = (
            "import sys; from tools import render_revised_lifecycle as figure; "
            "figure.check_outputs(); "
            "assert not {'matplotlib', 'fontTools', 'adaptive_timing'} & sys.modules.keys()"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=figure.ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
