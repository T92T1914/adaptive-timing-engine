import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from adaptive_timing.presentation import (appearance_css, report_hash,
                                          tokens, write_viewer)
from tools.render_saved_viewer import render, retained_report


def payload(document, name):
    return json.loads(document.split(f'<script type="application/json" id="{name}">', 1)[1].split('</script>', 1)[0])


class PresentationTests(unittest.TestCase):
    def test_retained_values_and_originals_survive_new_rendition(self):
        report, archive_hash = retained_report()
        original = copy.deepcopy(report)
        root = Path(__file__).resolve().parents[1]
        paths = list((root / "docs/evidence").iterdir()) + [root / "docs/adaptive-timing-example.svg"]
        before = {p: p.read_bytes() for p in paths}
        with tempfile.TemporaryDirectory() as directory:
            with patch('adaptive_timing.experiment.experiment', side_effect=AssertionError('No rerun')):
                identity = render(Path(directory))
            document = (Path(directory) / "explorer.html").read_text(encoding="utf-8")
            self.assertEqual(payload(document, "data"), original)
            self.assertEqual(payload(document, "presentation"), identity)
            self.assertEqual(identity["report_sha256"], report_hash(original))
            self.assertEqual(identity["archive"]["sha256"], archive_hash)
            self.assertIsNone(identity["evaluated_revision"])
            self.assertFalse(identity["experiment_rerun"])
            self.assertEqual(len(original["cases"]), 48)
            self.assertIn('<caption>Saved cases, without a new evaluation</caption>', document)
        self.assertEqual({p: p.read_bytes() for p in paths}, before)
        self.assertEqual(report, original)

    def test_local_six_faces_auto_and_print_have_explicit_contracts(self):
        css = appearance_css()
        self.assertEqual(css.count('@font-face'), 6)
        self.assertNotIn('url(', css)
        for face in ['Regular', 'SemiBold', 'Bold', 'Italic', 'SemiBoldItalic', 'BoldItalic']:
            self.assertIn(f'local("Inter-{face}")', css)
        self.assertIn(':root:not([data-appearance="clair"])', css)
        self.assertIn('@media print{:root,:root[data-appearance]', css)
        data = tokens()
        self.assertEqual(data['source']['revision'], '7a57fe750ff50205a17e1d342106a0d3f2777159')
        self.assertEqual(data['source']['sha256'], '889df65e0f4f4eea99a81c535d542e7332b537c000c332402a3ffa6a6cbb15b7')
        self.assertEqual({key: value['marker'] for key, value in data['states'].items()},
                         {'planned': 'circle', 'constrained': 'square', 'rejected': 'cross'})

    def test_synthetic_text_cannot_escape_data_or_provenance(self):
        report = {'cases': [{'scenario': '</script><script>bad</script>', 'seed': 0}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'viewer.html'
            write_viewer(report, output, provenance={'note': '<script>bad</script>'})
            document = output.read_text(encoding='utf-8')
            self.assertEqual(payload(document, 'data'), report)
            self.assertEqual(payload(document, 'presentation')['note'], '<script>bad</script>')
            self.assertNotIn('<script>bad</script>', document)

    def test_nonfinite_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'viewer.html'
            with self.assertRaises(ValueError):
                write_viewer({'value': float('nan')}, output)
            self.assertFalse(output.exists())

    def test_existing_export_api_renders_without_mutating_supplied_report(self):
        from adaptive_timing.experiment import write_viewer as public_write_viewer
        report, _ = retained_report()
        before = copy.deepcopy(report)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'viewer.html'
            public_write_viewer(report, output)
            text = output.read_text(encoding='utf-8')
            self.assertEqual(payload(text, 'data'), before)
            self.assertNotIn('__APPEARANCE_', text)
            self.assertEqual(payload(text, 'presentation')['report_sha256'], report_hash(before))
        self.assertEqual(report, before)

    def test_essential_text_and_state_marks_have_contrast(self):
        def luminance(color):
            rgb = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            return sum(v * weight for v, weight in zip(linear, [0.2126, 0.7152, 0.0722]))
        def contrast(a, b):
            hi, lo = sorted([luminance(a), luminance(b)], reverse=True)
            return (hi + 0.05) / (lo + 0.05)
        data = tokens()
        for mode, roles in data['themes'].items():
            for role in ['text', 'muted']:
                for background in ['canvas', 'panel']:
                    self.assertGreaterEqual(contrast(roles[role], roles[background]), 4.5)
            for state in data['states'].values():
                self.assertGreaterEqual(contrast(state[mode], roles['panel']), 3)


if __name__ == '__main__':
    unittest.main()
