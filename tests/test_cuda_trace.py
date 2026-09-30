import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_cuda_trace", ROOT / "tools/check_cuda_trace.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class TraceTests(unittest.TestCase):
    def test_retained_full_outputs_and_reject_wrong_bin(self):
        path = ROOT / "docs/cuda-application-trace.json"
        if not path.exists():
            self.skipTest("retained execution trace not yet delivered")
        record = json.loads(path.read_text())
        checker.validate(record)
        changed = copy.deepcopy(record)
        row = next(row for row in changed["results"] if row["dispatch"]["id"] == "current-repeat")
        row["value"]["value"][0][0][7] -= 1
        row["value"]["value"][0][0][8] += 1
        with self.assertRaises(AssertionError):
            checker.validate(changed)
