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
from adaptive_timing.revised_analysis import demonstration
from adaptive_timing.receipt_check import inspect_bytes
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
revised = demonstration()
assert revised['complete'] and revised['current']['total'] == 142336
assert revised['metrics']['admitted'] == 2
assert revised['metrics']['physical_values'] == 10240
assert revised['metrics']['stale'] == 1
inspection = inspect_bytes(json.dumps(revised).encode('utf-8'))
assert inspection['admission'] == 'accepted'
assert inspection['consistency'] == 'consistent'
assert inspection['completion'] == 'complete'
assert inspection['execution_authenticated'] is False
changed = json.loads(json.dumps(revised))
changed['results'][0]['value']['digest'] = '0' * 64
rejected = inspect_bytes(json.dumps(changed).encode('utf-8'))
assert rejected['consistency'] == 'contradictory'
assert any(issue['path'].endswith('.value.digest') for issue in rejected['issues'])
print('Unpacked wheel resources, isolated offline rendering and callable execution passed')
print('Unpacked wheel revised analysis, bounded refusal and current-result adoption passed')
print('Unpacked wheel passive receipt inspection and digest contradiction passed')
"""
        subprocess.run([sys.executable, "-I", "-c", source, str(root / "package")],
                       cwd=root, check=True)


if __name__ == "__main__":
    main()
