"""Inspect a built wheel and render supplied evidence outside the source checkout."""

from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    wheel = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with zipfile.ZipFile(wheel) as archive:
            for name in ("viewer.html", "appearance.js", "presentation.json", "py.typed"):
                assert "adaptive_timing/" + name in archive.namelist(), name
            archive.extractall(root / "package")
        # Isolated Python ignores user packages and PYTHONPATH. Only this wheel
        # copy is added, and the process cannot import the working checkout.
        source = """
from pathlib import Path
import json, sys
sys.path.insert(0, sys.argv[1])
from adaptive_timing.experiment import write_viewer
from adaptive_timing.presentation import tokens
from adaptive_timing.execution import RealExecutor
from adaptive_timing.runtime import Dispatch
output = Path('viewer.html')
write_viewer({'cases': []}, output)
text = output.read_text(encoding='utf-8')
assert '__APPEARANCE_' not in text
assert 'local("Inter-SemiBoldItalic")' in text
assert tokens()['source']['revision'] == '7a57fe750ff50205a17e1d342106a0d3f2777159'
with RealExecutor() as executor:
    executor.submit(Dispatch('wheel', 'cpu', 0, 0.1, 'dispatched'), 1, lambda cancel: 42)
    result, = executor.wait(1, timeout=2)
    assert result.usable and result.value == 42
print('Unpacked wheel resources, isolated offline rendering and callable execution passed')
"""
        subprocess.run([sys.executable, "-I", "-c", source, str(root / "package")],
                       cwd=root, check=True)


if __name__ == "__main__":
    main()
