"""Build a public site from an explicit list of public example files."""

from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adaptive_timing.presentation import ROOT as PRESENTATION_ROOT, appearance_css
from tools.render_saved_viewer import render
OUT = ROOT / "_site"
FILES = {
    "site/index.html": "index.html",
    "site/style.css": "style.css",
    "site/app.js": "app.js",
    "docs/visual-example-data.json": "data.json",
    "docs/adaptive-timing-example.svg": "example.svg",
}


def main():
    data = json.loads((ROOT / "docs/visual-example-data.json").read_text())
    if not data.get("source_commit"):
        raise ValueError("Example data must retain its source revision.")
    OUT.mkdir(exist_ok=True)
    for source, target in FILES.items():
        path = ROOT / source
        if not path.is_file() or path.is_symlink():
            raise ValueError("Expected a regular source file: " + source)
        shutil.copyfile(path, OUT / target)
    (OUT / "appearance.css").write_text(appearance_css(), encoding="utf-8", newline="\n")
    shutil.copyfile(PRESENTATION_ROOT / "appearance.js", OUT / "appearance.js")
    render(OUT)
    generated = {"appearance.css", "appearance.js", "explorer.html", "presentation.json"}
    unexpected = {p.name for p in OUT.iterdir()} - set(FILES.values()) - generated
    if unexpected:
        raise ValueError("Unexpected site output files: " + str(sorted(unexpected)))
    print("Built", len(FILES) + len(generated), "public files in", OUT)


if __name__ == "__main__":
    main()
