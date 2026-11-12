import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "adaptive_timing"

# The explicit optional adapter for the native runtime is the one module that
# may import something outside the standard library.
OPTIONAL = {"cuda"}

# Isolated mode ignores PYTHONPATH, so the probe adds the checkout itself.
PROBE = """
import importlib, json, sys
sys.path.insert(0, sys.argv[1])
before = set(sys.modules)
for name in json.loads(sys.argv[2]):
    importlib.import_module("adaptive_timing." + name)
loaded = {m.split(".")[0] for m in set(sys.modules) - before}
print(json.dumps(sorted(m for m in loaded
                        if m != "adaptive_timing" and not m.startswith("_")
                        and m not in sys.stdlib_module_names)))
"""


class StandardLibraryOnlyTests(unittest.TestCase):
    def test_default_modules_import_nothing_outside_the_standard_library(self):
        names = sorted(p.stem for p in PACKAGE.glob("*.py")
                       if p.stem != "__init__" and p.stem not in OPTIONAL)
        self.assertIn("model", names)
        result = subprocess.run(
            [sys.executable, "-I", "-c", PROBE, str(ROOT), json.dumps(names)],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])


if __name__ == "__main__":
    unittest.main()
