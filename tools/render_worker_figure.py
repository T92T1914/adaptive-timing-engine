"""Render the retained worker example without starting a worker or experiment."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = "docs/visual-example-data.json"
MANIFEST = "docs/adaptive-timing-figure.json"
INPUTS = (DATA, "adaptive_timing/presentation.json", "tools/render_worker_figure.py")
FACES = {
    "Regular": (400, False),
    "SemiBold": (600, False),
    "Bold": (700, False),
    "Italic": (400, True),
    "SemiBoldItalic": (600, True),
    "BoldItalic": (700, True),
}
EVENTS = (
    (
        "start",
        "Start payload 0",
        "Calculation stays active.\nA thread event holds its result.",
        "accent",
    ),
    (
        "replace",
        "Requests 1 to 100 arrive",
        "One waiting slot.\n99 requests replaced.",
        "warning",
    ),
    (
        "reject",
        "Release the first calculation",
        "Payload 0 completes.\nIts obsolete result is rejected.",
        "error",
    ),
    (
        "publish",
        "Compute payload 100",
        "Current result kept:\npayload 100, revision 101.",
        "success",
    ),
)


def digest(path, text=False):
    data = path.read_text(encoding="utf-8").encode() if text else path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def load_data():
    data = json.loads((ROOT / DATA).read_text(encoding="utf-8"))
    expected = {
        "generation": 101,
        "submitted": 101,
        "computed": 2,
        "replaced": 99,
        "stale": 1,
        "failures": 0,
        "queued": 0,
        "running": False,
    }
    if (
        data["source_commit"] != "6e12229"
        or data["source"] != "adaptive_timing.experiment.worker_experiment"
        or type(data["request_count"]) is not int
        or data["request_count"] != 101
        or data["computed_payloads"] != [0, 100]
        or any(type(v) is not int for v in data["computed_payloads"])
        or data["stats"] != expected
        or any(type(data["stats"][k]) is not type(v) for k, v in expected.items())
        or data["timeline"]
        != "Schematic event order; horizontal spacing does not represent elapsed time"
    ):
        raise ValueError(
            "The diagram requires the retained worker example and counters"
        )
    return data


def font_files(directory):
    """Require actual static Inter faces, without a fallback or font download."""
    from fontTools.ttLib import TTFont

    files, evidence = {}, {}
    for name, (weight, italic) in FACES.items():
        path = directory / f"Inter-{name}.ttf"
        with TTFont(path) as font:
            postscript = font["name"].getDebugName(6)
            if (
                postscript != f"Inter-{name}"
                or font["OS/2"].usWeightClass != weight
                or bool(font["OS/2"].fsSelection & 1) != italic
                or not set(range(32, 127)).issubset(font.getBestCmap())
            ):
                raise ValueError(
                    f"Incorrect Inter face or missing Latin glyphs: {path.name}"
                )
            evidence[name] = {
                "sha256": digest(path),
                "postscript": postscript,
                "weight": weight,
                "italic": italic,
            }
        files[name] = path
    return files, evidence


def semantic_record(data):
    return {
        "retained_worker_example": data,
        "experiment_rerun": False,
        "sequence": [
            dict(id=key, title=title, explanation=body, role=role)
            for key, title, body, role in EVENTS
        ],
        "spacing": "Event order only, not elapsed time",
        "separate_trace_report_evaluated_revision": None,
    }


def draw(data, tokens, mode, files, output, wide=False):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    roles = tokens["themes"][mode]
    fonts = {name: FontProperties(fname=str(path)) for name, path in files.items()}
    with rc_context({"svg.fonttype": "path", "svg.hashsalt": "timing-worker-v1"}):
        fig = Figure(
            figsize=(9, 6.4) if wide else (4.8, 11.2),
            dpi=200,
            facecolor=roles["canvas"],
        )
        labels = []

        def label(x, y, text, size=13, face="Regular", color=None):
            item = fig.text(
                x,
                y,
                text,
                fontsize=size,
                fontproperties=fonts[face],
                color=color or roles["text"],
                va="top",
                linespacing=1.3,
            )
            labels.append(item)
            return item

        if wide:
            label(
                0.055, 0.96, "ADAPTIVE TIMING ENGINE", 12, "SemiBold", roles["accent"]
            )
            label(0.055, 0.90, "Keep the current result.", 27, "Bold")
            label(
                0.055,
                0.81,
                "101 requests. 2 calculations. One active, one waiting.",
                14,
            )
            # Read left to right in each numbered row. Arrows preserve event order.
            positions = [(0.055, 0.70), (0.535, 0.70), (0.055, 0.46), (0.535, 0.46)]
            for index, ((key, title, body, role), (x, top)) in enumerate(
                zip(EVENTS, positions, strict=True)
            ):
                fig.add_artist(
                    FancyBboxPatch(
                        (x, top - 0.17),
                        0.41,
                        0.17,
                        transform=fig.transFigure,
                        boxstyle="round,pad=0.008,rounding_size=0.01",
                        facecolor=roles["panel"],
                        edgecolor=roles["border"],
                        linewidth=0.8,
                        gid=f"event-{key}",
                    )
                )
                label(
                    x + 0.012,
                    top - 0.015,
                    f"{index + 1}. {title}",
                    13,
                    "SemiBold",
                    roles[role],
                )
                label(x + 0.012, top - 0.067, body, 13)
                if index < 3:
                    start, end = ((x + 0.42, top - 0.085), (x + 0.46, top - 0.085))
                    if index == 1:
                        start, end = ((0.74, 0.52), (0.26, 0.47))
                    fig.add_artist(
                        FancyArrowPatch(
                            start,
                            end,
                            transform=fig.transFigure,
                            arrowstyle="-|>",
                            mutation_scale=12,
                            color=roles["muted"],
                            gid=f"next-{key}",
                        )
                    )
            label(0.055, 0.24, "0 failures. Nothing queued or running.", 13, "SemiBold")
            label(
                0.055,
                0.17,
                "Event order, not elapsed time. No speedup or deadline guarantee.",
                13,
                color=roles["muted"],
            )
            label(
                0.055,
                0.10,
                "Controlled example, no new workload. Recorded source: 6e12229.",
                13,
                "Italic",
                roles["muted"],
            )
        else:
            label(
                0.065, 0.96, "ADAPTIVE TIMING ENGINE", 12, "SemiBold", roles["accent"]
            )
            label(0.065, 0.915, "Keep the current\nresult.", 27, "Bold")
            label(0.065, 0.815, "One calculation active.\nOne request waiting.", 14)
            label(0.065, 0.755, "101 requests", 17, "Bold")
            label(0.535, 0.755, "2 calculations", 17, "Bold")
            for index, (key, title, body, role) in enumerate(EVENTS):
                top = 0.705 - index * 0.14
                box = FancyBboxPatch(
                    (0.065, top - 0.112),
                    0.87,
                    0.112,
                    transform=fig.transFigure,
                    boxstyle="round,pad=0.008,rounding_size=0.01",
                    facecolor=roles["panel"],
                    edgecolor=roles["border"],
                    linewidth=0.8,
                    gid=f"event-{key}",
                )
                fig.add_artist(box)
                label(
                    0.09,
                    top - 0.012,
                    f"{index + 1}. {title}",
                    13,
                    "SemiBold",
                    roles[role],
                )
                label(0.09, top - 0.046, body, 13)
                if index < len(EVENTS) - 1:
                    arrow = FancyArrowPatch(
                        (0.5, top - 0.122),
                        (0.5, top - 0.13),
                        transform=fig.transFigure,
                        arrowstyle="-|>",
                        mutation_scale=10,
                        color=roles["muted"],
                        gid=f"next-{key}",
                    )
                    fig.add_artist(arrow)
            label(0.065, 0.145, "0 failures. Nothing queued or running.", 12.5)
            label(
                0.065,
                0.111,
                "Event order, not elapsed time.\nNo speedup or deadline guarantee.\nRecorded source: 6e12229.",
                12.5,
                color=roles["muted"],
            )
            label(
                0.065,
                0.037,
                "Controlled example, no new workload.",
                12,
                "Italic",
                roles["muted"],
            )
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        # Catch cropped labels before publishing either edition.
        bounds = [item.get_window_extent(canvas.get_renderer()) for item in labels]
        if any(
            b.x0 < 0 or b.y0 < 0 or b.x1 > fig.bbox.width or b.y1 > fig.bbox.height
            for b in bounds
        ):
            raise ValueError("A figure label extends outside the canvas")
        layout = {
            "width": int(fig.bbox.width),
            "height": int(fig.bbox.height),
            "labels_within_canvas": len(bounds),
            "minimum_font_points": min(item.get_fontsize() for item in labels),
        }
        for ext in ("png", "svg"):
            metadata = {
                "Description": json.dumps(semantic_record(data), sort_keys=True)
            }
            if ext == "svg":
                metadata["Date"] = None
            suffix = "-wide" if wide else ""
            path = output / f"adaptive-timing-{mode}{suffix}.{ext}"
            fig.savefig(path, metadata=metadata)
            if ext == "svg":
                path.write_text(
                    "\n".join(line.rstrip() for line in path.read_text().splitlines())
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
        fig.clear()
        return layout


def check_outputs():
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if record["evidence"] != semantic_record(load_data()):
        raise ValueError("Figure evidence is stale")
    for name in INPUTS:
        if record["inputs_sha256_lf"][name] != digest(ROOT / name, text=True):
            raise ValueError(f"Figure input changed: {name}")
    expected = {
        f"docs/adaptive-timing-{mode}{suffix}.{ext}"
        for mode in ("clair", "obscur")
        for suffix in ("", "-wide")
        for ext in ("png", "svg")
    }
    if set(record["outputs_sha256"]) != expected:
        raise ValueError("Both PNG and SVG editions are required")
    for name, checksum in record["outputs_sha256"].items():
        if digest(ROOT / name) != checksum:
            raise ValueError(f"Figure output changed: {name}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--font-dir", type=Path, help="Directory of six official static Inter TTFs"
    )
    parser.add_argument(
        "--check", action="store_true", help="Verify committed output without rendering"
    )
    args = parser.parse_args()
    if args.check:
        check_outputs()
        print("Both editions match the retained worker evidence and renderer")
        return
    if args.font_dir is None:
        parser.error(
            "--font-dir is required for rendering; no font download is performed"
        )
    data = load_data()
    tokens = json.loads((ROOT / "adaptive_timing/presentation.json").read_text())
    files, font_evidence = font_files(args.font_dir)
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)
        layouts = {
            mode: draw(data, tokens, mode, files, output)
            for mode in ("clair", "obscur")
        }
        wide_layouts = {
            mode: draw(data, tokens, mode, files, output, wide=True)
            for mode in ("clair", "obscur")
        }
        import matplotlib

        record = {
            "schema_version": 2,
            "evidence": semantic_record(data),
            "renderer": {"matplotlib": matplotlib.__version__, "backend": "Agg"},
            "layout": layouts,
            "wide_layout": wide_layouts,
            "typography": {
                "files": font_evidence,
                "painted_faces": ["Regular", "SemiBold", "Bold", "Italic"],
                "png": "Glyphs rasterized from explicit Inter files",
                "svg": "Labels outlined from the same Inter files. Selectable text and source data accompany the figure",
            },
            "inputs_sha256_lf": {
                name: digest(ROOT / name, text=True) for name in INPUTS
            },
            "outputs_sha256": {
                "docs/" + p.name: digest(p) for p in sorted(output.iterdir())
            },
        }
        for path in output.iterdir():
            (ROOT / "docs" / path.name).write_bytes(path.read_bytes())
        (ROOT / MANIFEST).write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    check_outputs()
    print("Rendered both editions from retained data with verified Inter files")


if __name__ == "__main__":
    main()
