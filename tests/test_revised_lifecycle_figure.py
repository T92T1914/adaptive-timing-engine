"""Keep the new lifecycle figure tied to its public saved evidence."""

import json
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
        self.assertEqual(record["layout"]["clair"], record["layout"]["obscur"])
        layout = record["layout"]["obscur"]
        self.assertEqual((layout["width"], layout["height"]), (1520, 4400))
        self.assertGreaterEqual(layout["minimum_font_points"], 24)
        self.assertGreaterEqual(layout["minimum_css_pixels_at_narrow_width"], 15)

    def test_svg_editions_preserve_order_geometry_and_text_evidence(self):
        record = figure.check_outputs()
        roots = [ET.parse(figure.ROOT / f"docs/revised-lifecycle-{mode}.svg").getroot()
                 for mode in ("clair", "obscur")]
        for root in roots:
            self.assertEqual(json.loads(root.find(".//dc:description", NS).text),
                             record["evidence"])
            self.assertGreater(len(root.findall(".//s:g[@id]", NS)), 5)
        for key, _, _, _, _ in figure.STAGES:
            paths = [root.find(f".//s:g[@id='stage-{key}']/s:path", NS).get("d")
                     for root in roots]
            self.assertEqual(paths[0], paths[1])
        for mode in ("clair", "obscur"):
            text = (figure.ROOT / f"docs/revised-lifecycle-{mode}.svg").read_text()
            for face in ("Regular", "SemiBold", "Bold", "Italic"):
                self.assertIn(f'id="Inter-{face}-', text)
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
