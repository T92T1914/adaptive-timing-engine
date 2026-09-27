"""Create a new viewer rendition from retained traces, without another experiment."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing.presentation import report_hash, write_viewer

SOURCE = "docs/evidence/traces.json.gz"
ARCHIVE_REVISION = "b50c6e7d1fb6ceb7a7917b989dd193a4dead8547"


def retained_report():
    compressed = (ROOT / SOURCE).read_bytes()
    report = json.loads(gzip.decompress(compressed))
    old_html = (ROOT / "docs/evidence/demo.html").read_text(encoding="utf-8")
    old_payload = old_html.split('<script type="application/json" id="data">', 1)[1].split('</script>', 1)[0]
    if report != json.loads(old_payload):
        raise ValueError("Retained HTML and compressed trace payload differ")
    return report, hashlib.sha256(compressed).hexdigest()


def render(output: Path):
    report, archive_hash = retained_report()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    provenance = {
        "kind": "new presentation of retained experiment",
        "experiment_rerun": False,
        "evaluated_revision": None,
        "evaluated_revision_note": "The saved report did not record an evaluated Git revision.",
        "archive": {"path": SOURCE, "sha256": archive_hash,
                    "introduced_at": ARCHIVE_REVISION,
                    "note": "Archive commit identifies retained bytes, not the evaluated source revision."},
        "rendered_revision": revision,
        "renderer_has_uncommitted_changes": bool(dirty),
        "report_sha256": report_hash(report),
    }
    output.mkdir(parents=True, exist_ok=True)
    identity = write_viewer(report, output / "explorer.html", provenance=provenance)
    (output / "presentation.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8", newline="\n")
    return identity


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/retained"))
    args = parser.parse_args()
    identity = render(args.output)
    print(f"Rendered retained report {identity['report_sha256']} in {args.output}")
