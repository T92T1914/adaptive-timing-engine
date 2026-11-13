"""Check the retained diagram, its actual output and public consumers."""

import copy
import json
import re
import struct
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
        narrow, wide = record["layout"]["narrow"], record["layout"]["wide"]
        self.assertEqual((narrow["width"], wide["width"]), (960, 1800))
        self.assertGreaterEqual(narrow["minimum_font_points"], 33)
        self.assertGreaterEqual(wide["minimum_font_points"], 30)
        self.assertEqual(record["rules"]["version"], "1.0.0")
        self.assertEqual(
            sorted(record["typography"]["files"]), ["Bold", "Regular", "SemiBold"]
        )
        for face, (weight, italic) in figure.FACES.items():
            item = record["typography"]["files"][face]
            self.assertEqual(
                (item["postscript"], item["weight"], item["italic"]),
                ("Inter-" + face, weight, italic),
            )
        self.assertFalse(record["retained_alternates"]["rebuilt"])

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

    def test_obscur_editions_keep_order_geometry_and_full_evidence(self):
        narrow = ET.parse(figure.ROOT / "docs/adaptive-timing-obscur.svg").getroot()
        wide = ET.parse(figure.ROOT / "docs/adaptive-timing-obscur-wide.svg").getroot()
        places = {}
        for name, root in (("narrow", narrow), ("wide", wide)):
            places[name] = []
            for key, _, _, _ in figure.EVENTS:
                path = root.find(f".//s:g[@id='event-{key}']/s:path", NS).get("d")
                xy = re.match(r"M ([\d.]+) ([\d.]+)", path)
                places[name].append(tuple(map(float, xy.groups())))
            for key in ("start", "replace", "reject"):
                self.assertEqual(len(root.findall(f".//s:g[@id='next-{key}']", NS)), 1)
            self.assertEqual(
                json.loads(root.find(".//dc:description", NS).text),
                figure.semantic_record(figure.load_data()),
            )
        column = places["narrow"]
        self.assertEqual({x for x, _ in column}, {column[0][0]})
        self.assertEqual(column, sorted(column, key=lambda item: item[1]))
        row = places["wide"]
        self.assertLess(row[0][0], row[1][0])
        self.assertLess(row[1][0], row[2][0])
        self.assertEqual({y for _, y in row[:3]}, {row[0][1]})
        self.assertLess(row[0][1], row[3][1])
        self.assertEqual(row[3][0], row[0][0])
        self.assertIsNotNone(wide.find(".//s:g[@id='focus-publish']", NS))

    def test_obscur_uses_only_the_monochrome_tokens_and_inter_outlines(self):
        allowed = set(json.loads(
            (figure.ROOT / figure.TOKENS_PATH).read_text())["colors"].values())
        for name in figure.OBSCUR:
            if not name.endswith(".svg"):
                continue
            svg = (figure.ROOT / name).read_text()
            self.assertLessEqual(set(re.findall(r"#[0-9a-fA-F]{6}", svg)), allowed)
            for face in ("Regular", "SemiBold", "Bold"):
                self.assertIn(f'id="Inter-{face}-', svg)
            self.assertNotIn("Italic", svg)
            self.assertNotIn("DejaVu", svg)
            self.assertNotIn("<text", svg)
            self.assertNotIn("@font-face", svg)
            for node in ET.fromstring(svg).iter():
                for key, value in node.attrib.items():
                    if key.endswith("href"):
                        self.assertTrue(value.startswith("#"))

    def test_png_editions_have_the_rule_dimensions(self):
        record = figure.check_outputs()
        for name, edition in (
            ("docs/adaptive-timing-obscur.png", "narrow"),
            ("docs/adaptive-timing-obscur-wide.png", "wide"),
        ):
            data = (figure.ROOT / name).read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            size = struct.unpack(">II", data[16:24])
            layout = record["layout"][edition]
            self.assertEqual(size, (layout["width"], layout["height"]))

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
