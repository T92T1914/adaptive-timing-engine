"""Check the retained diagram, its actual output and public consumers."""

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from tools import render_worker_figure as figure

NS = {"s": "http://www.w3.org/2000/svg", "dc": "http://purl.org/dc/elements/1.1/"}


class WorkerFigureTests(unittest.TestCase):
    def test_checked_counter_identity_and_separate_unknown_revision(self):
        record = figure.check_outputs()
        evidence = record["evidence"]
        data = evidence["retained_worker_example"]
        self.assertEqual(data["source_commit"], "6e12229")
        self.assertFalse(evidence["experiment_rerun"])
        self.assertIsNone(evidence["separate_trace_report_evaluated_revision"])
        self.assertEqual(data["request_count"], 101)
        self.assertEqual(data["stats"]["replaced"] + data["stats"]["computed"], 101)
        self.assertEqual(data["computed_payloads"], [0, 100])
        self.assertEqual(data["stats"]["stale"], 1)
        self.assertEqual(record["layout"]["clair"], record["layout"]["obscur"])
        self.assertEqual(record["layout"]["clair"]["width"], 960)
        self.assertEqual(record["layout"]["clair"]["height"], 2240)
        self.assertGreaterEqual(record["layout"]["clair"]["minimum_font_points"], 12)
        for face, (weight, italic) in figure.FACES.items():
            item = record["typography"]["files"][face]
            self.assertEqual(
                (item["postscript"], item["weight"], item["italic"]),
                ("Inter-" + face, weight, italic),
            )

    def test_originals_remain_identical_with_text_checkout_normalization(self):
        for name, checksum, text in (
            (
                "docs/adaptive-timing-example.png",
                "38be9d4dd291cc9526a707f032be87ac1a1e62853857417f1e8df1b95f75306d",
                False,
            ),
            (
                "docs/adaptive-timing-example.svg",
                "a4af841b9930053bd428decc80eb962f98a7f479f47edb0d0ed6e659cc888f5e",
                True,
            ),
            (
                figure.DATA,
                "a40b87d2ed6e034ce6bc7aad59467f5c90e990a4bfb6a3f523dfab978d120429",
                True,
            ),
        ):
            self.assertEqual(figure.digest(figure.ROOT / name, text=text), checksum)

    def test_svg_editions_have_same_sequence_geometry_and_full_evidence(self):
        roots = [
            ET.parse(figure.ROOT / f"docs/adaptive-timing-{mode}.svg").getroot()
            for mode in ("clair", "obscur")
        ]
        for key, _, _, _ in figure.EVENTS:
            paths = [
                root.find(f".//s:g[@id='event-{key}']/s:path", NS).get("d")
                for root in roots
            ]
            self.assertEqual(paths[0], paths[1])
        for root in roots:
            metadata = json.loads(root.find(".//dc:description", NS).text)
            self.assertEqual(metadata, figure.semantic_record(figure.load_data()))
            self.assertEqual(len(root.findall(".//s:g[@id='next-start']", NS)), 1)
            self.assertEqual(len(root.findall(".//s:g[@id='next-replace']", NS)), 1)
            self.assertEqual(len(root.findall(".//s:g[@id='next-reject']", NS)), 1)

    def test_wide_sequence_keeps_order_counters_and_glyphs(self):
        for mode in ("clair", "obscur"):
            raw = (figure.ROOT / f"docs/adaptive-timing-{mode}-wide.svg").read_text()
            root = ET.fromstring(raw)
            positions = []
            for index, (key, title, _, _) in enumerate(figure.EVENTS):
                path = root.find(f".//s:g[@id='event-{key}']/s:path", NS).get("d")
                xy = re.match(r"M ([\d.]+) ([\d.]+)", path)
                positions.append(tuple(map(float, xy.groups())))
                self.assertIn(f"<!-- {index + 1}. {title} -->", raw)
            self.assertLess(positions[0][0], positions[1][0])
            self.assertEqual(positions[0][1], positions[1][1])
            self.assertLess(positions[0][1], positions[2][1])
            self.assertEqual(positions[0][0], positions[2][0])
            self.assertEqual(positions[1][0], positions[3][0])
            self.assertEqual(positions[2][1], positions[3][1])
            for key in ("start", "replace", "reject"):
                self.assertIsNotNone(root.find(f".//s:g[@id='next-{key}']", NS))
            for text in (
                "99 requests replaced.",
                "payload 100, revision 101.",
                "Its obsolete result is rejected.",
            ):
                self.assertIn(text, raw)
            self.assertEqual(
                json.loads(root.find(".//dc:description", NS).text),
                figure.semantic_record(figure.load_data()),
            )
            for face in ("Regular", "SemiBold", "Bold", "Italic"):
                self.assertIn(f'xlink:href="#Inter-{face}-', raw)
            self.assertNotIn("<text", raw)

    def test_real_inter_outlines_and_no_external_glyph_requests(self):
        for mode in ("clair", "obscur"):
            svg = (figure.ROOT / f"docs/adaptive-timing-{mode}.svg").read_text()
            for face in ("Regular", "SemiBold", "Bold", "Italic"):
                self.assertIn(f'id="Inter-{face}-', svg)
            self.assertNotIn("DejaVu", svg)
            self.assertNotIn("<text", svg)
            self.assertNotIn("@font-face", svg)
            for node in ET.fromstring(svg).iter():
                for name, value in node.attrib.items():
                    if name.endswith("href"):
                        self.assertTrue(value.startswith("#"))

    def test_changed_counters_boolean_aliases_or_timing_claims_are_rejected(self):
        for key in ("count", "stale", "bool", "payload", "revision", "time"):
            data = copy.deepcopy(figure.load_data())
            if key == "count":
                data["request_count"] = 100
            elif key == "stale":
                data["stats"]["stale"] = 0
            elif key == "bool":
                data["stats"]["running"] = 0
            elif key == "payload":
                data["computed_payloads"] = [0, 99]
            elif key == "revision":
                data["source_commit"] = "unknown"
            else:
                data["timeline"] = "Elapsed execution time"
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "docs").mkdir()
                (root / figure.DATA).write_text(json.dumps(data))
                with patch.object(figure, "ROOT", root), self.assertRaises(ValueError):
                    figure.load_data()

    def test_render_requires_explicit_fonts(self):
        result = subprocess.run(
            [sys.executable, "tools/render_worker_figure.py"],
            cwd=figure.ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--font-dir is required", result.stderr)

    def test_public_consumers_use_new_editions_and_keep_original(self):
        for name in ("README.md", "docs/visual-example.md"):
            content = (figure.ROOT / name).read_text()
            self.assertIn("<picture>", content)
            for mode in ("clair", "obscur"):
                self.assertIn(f"adaptive-timing-{mode}.png", content)
        page = (figure.ROOT / "site/index.html").read_text()
        for mode in ("clair", "obscur"):
            self.assertIn(f'src="worker-{mode}.png"', page)
            self.assertIn(f'href="worker-{mode}.svg" download', page)
        self.assertIn('href="example.svg"', page)


if __name__ == "__main__":
    unittest.main()
