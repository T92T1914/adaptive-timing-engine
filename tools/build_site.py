"""Build a public site from an explicit list of public example files."""

from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing.presentation import ROOT as PRESENTATION_ROOT, appearance_css
from tools.render_saved_viewer import render
from tools.render_worker_figure import check_outputs
from tools.render_causal_report import render as render_causal
OUT = ROOT / "_site"
FILES = {
    "site/share-preview.png": "share-preview.png",
    "docs/adaptive-timing-clair-wide.png": "worker-clair-wide.png",
    "docs/adaptive-timing-clair-wide.svg": "worker-clair-wide.svg",
    "docs/adaptive-timing-obscur-wide.png": "worker-obscur-wide.png",
    "docs/adaptive-timing-obscur-wide.svg": "worker-obscur-wide.svg",
    "site/index.html": "index.html",
    "site/style.css": "style.css",
    "site/app.js": "app.js",
    "docs/visual-example-data.json": "data.json",
    "docs/adaptive-timing-example.svg": "example.svg",
    "docs/adaptive-timing-clair.png": "worker-clair.png",
    "docs/adaptive-timing-obscur.png": "worker-obscur.png",
    "docs/adaptive-timing-clair.svg": "worker-clair.svg",
    "docs/adaptive-timing-obscur.svg": "worker-obscur.svg",
    "docs/adaptive-timing-figure.json": "worker-figure.json",
    "docs/causal-evidence/summary.json": "causal-summary.json",
    "docs/causal-evidence/raw.json.gz": "causal-raw.json.gz",
}


def main():
    from tools.render_share_preview import check as check_share_preview

    check_share_preview()
    data = json.loads((ROOT / "docs/visual-example-data.json").read_text())
    if not data.get("source_commit"):
        raise ValueError("Example data must retain its source revision.")
    check_outputs()
    OUT.mkdir(exist_ok=True)
    for source, target in FILES.items():
        path = ROOT / source
        if not path.is_file() or path.is_symlink():
            raise ValueError("Expected a regular source file: " + source)
        shutil.copyfile(path, OUT / target)
    (OUT / "appearance.css").write_text(appearance_css(), encoding="utf-8", newline="\n")
    shutil.copyfile(PRESENTATION_ROOT / "appearance.js", OUT / "appearance.js")
    render(OUT)
    render_causal(OUT)
    generated = {"appearance.css", "appearance.js", "explorer.html", "presentation.json",
                 "causal.html", "causal-presentation.json"}
    unexpected = {p.name for p in OUT.iterdir()} - set(FILES.values()) - generated
    if unexpected:
        raise ValueError("Unexpected site output files: " + str(sorted(unexpected)))
    print("Built", len(FILES) + len(generated), "public files in", OUT)


if __name__ == "__main__":
    main()
