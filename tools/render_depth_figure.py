"""Draw time spent after the newer request against queue depth, on Obscur, from the saved runs.

    python tools/render_depth_figure.py --font-dir DIRECTORY
    python tools/render_depth_figure.py --check

The values are the metric stale_after_newer_ms_per_stop of tools/gpu_depth_run_tables.py.
Each point is the median over the arrival conditions of one chunk size and depth that have
a value, and each bar spans the lowest and highest of them. Drawing needs the packages in
requirements-figures.txt. The check needs only the standard library.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gpu_depth_run_tables import comparisons  # noqa: E402
from obscur_figure import FACES, TOKENS_PATH, Measure, digest, font_files, pixels, tokens  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RUNS = "docs/evidence/gpu-depth-runs.json"
PREDICTIONS = "docs/evidence/gpu-depth-predictions.json"
MANIFEST = "docs/gpu-queue-depth-figure.json"
INPUTS = (RUNS, PREDICTIONS, TOKENS_PATH, "tools/obscur_figure.py", "tools/gpu_depth_run_tables.py",
          "tools/render_depth_figure.py")
OUTPUTS = tuple(f"docs/gpu-queue-depth-obscur{edition}.{extension}"
                for edition in ("", "-wide") for extension in ("png", "svg"))
METRIC = "stale_after_newer_ms_per_stop"
DEPTHS = (1, 2, 4, 8, None)
TOP_MS = 2.0
TICKS = (0.0, 0.5, 1.0, 1.5, 2.0)
TITLE = "Work still running after a newer request"
SUBTITLE = ("Mean time from the newer request to the end of a stopped pass (ms), by queue depth. "
            "Solid lines are measured. Dashed lines are predicted.")
FOOTER = ("Median over the arrival conditions with a value. Bars span lowest to highest. "
          "None: the model predicts no stop. One card, generated arrivals, no latency bound.")


def load_data():
    runs = json.loads((ROOT / RUNS).read_text(encoding="utf-8"))
    predictions = json.loads((ROOT / PREDICTIONS).read_text(encoding="utf-8"))
    rows = comparisons(runs, predictions)
    panels = []
    for size in sorted({row["chunk_blocks"] for row in rows}):
        group = [row for row in rows if row["chunk_blocks"] == size]
        panel = {"chunk_blocks": size, "chunks_per_pass": group[0]["chunks_per_pass"], "points": []}
        for depth in DEPTHS:
            point = {"depth": "all" if depth is None else depth}
            for side in ("measured", "predicted"):
                values = [row["metrics"][METRIC][side] for row in group
                          if row["depth"] == depth and row["metrics"][METRIC][side] is not None]
                point[side] = ({"median": statistics.median(values), "low": min(values), "high": max(values),
                                "conditions": len(values)} if values else None)
                if values and max(values) > TOP_MS:
                    raise ValueError("A value is above the top of the axis")
            panel["points"].append(point)
        panels.append(panel)
    return panels


def semantic_record(panels):
    return {"metric": METRIC, "axis_top_ms": TOP_MS, "panels": panels,
            "text": [TITLE, SUBTITLE, FOOTER]}


def draw(panels, wide, files, output, stem, description):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle

    rules = tokens()
    colors, space = rules["colors"], rules["space"]
    name = "wide" if wide else "narrow"
    edition, points = space[name], rules["type"]["points"][name]
    width, margin = edition["width"], edition["margin"]
    inner = width - 2 * margin
    measure = Measure(files)
    body = pixels(points["body"])
    pitch = body * rules["type"]["line_spacing"]
    labels, lines, marks = [], [], []

    def put(x, y, text, role, face, color, limit, align="left"):
        size = pixels(points[role])
        wrapped = measure.wrap(text, face, size, limit)
        for index, line in enumerate(wrapped):
            labels.append((x, y + index * size * rules["type"]["line_spacing"], line, face,
                           points[role], color, align))
        return len(wrapped) * size * rules["type"]["line_spacing"]

    def line(x0, y0, x1, y1, color, stroke, dashed=False):
        lines.append((x0, y0, x1, y1, color, stroke, dashed))

    y = margin
    y += put(margin, y, TITLE, "title", "Bold", colors["text"], inner) + 14
    y += put(margin, y, SUBTITLE, "subtitle", "Regular", colors["muted"], inner) + 44
    columns = 2 if wide else 1
    gap = space["wide"]["gap"] if wide else 48
    panel_width = (inner - gap * (columns - 1)) / columns
    plot_height = 460
    axis_room = 100
    panel_height = pitch + 30 + plot_height + 14 + 2 * pitch
    for number, panel in enumerate(panels):
        left = margin + (number % columns) * (panel_width + gap)
        top = y + (number // columns) * (panel_height + 56)
        size = panel["chunk_blocks"]
        put(left, top, f"{size} block{'s' if size > 1 else ''} per chunk, {panel['chunks_per_pass']} chunks",
            "card_title", "SemiBold", colors["text"], panel_width)
        plot_top = top + pitch + 30
        base = plot_top + plot_height
        plot_left = left + axis_room
        plot_width = panel_width - axis_room

        def height(value):
            return base - value / TOP_MS * plot_height

        for tick in TICKS:
            if tick:
                line(plot_left, height(tick), left + panel_width, height(tick), colors["divider"], 1)
            put(plot_left - 14, height(tick) - body * 0.6, f"{tick:.1f}", "body", "Regular",
                colors["text"], axis_room, "right")
        line(plot_left, plot_top, plot_left, base, colors["border"], 2)
        line(plot_left, base, left + panel_width, base, colors["border"], 2)
        slot = plot_width / len(DEPTHS)
        previous = {}
        for index, point in enumerate(panel["points"]):
            center = plot_left + slot * (index + 0.5)
            put(center, base + 14, str(point["depth"]), "body", "Regular", colors["text"], slot, "center")
            for side, shift in (("predicted", -18), ("measured", 18)):
                value = point[side]
                if value is None:
                    previous.pop(side, None)
                    if side == "predicted":
                        put(center - 18, base - pitch, "none", "body", "Regular", colors["text"], slot,
                            "center")
                    continue
                x = center + shift
                dashed = side == "predicted"
                line(x, height(value["low"]), x, height(value["high"]), colors["text"], 2, dashed)
                for end in ("low", "high"):
                    line(x - 9, height(value[end]), x + 9, height(value[end]), colors["text"], 2)
                here = (x, height(value["median"]))
                if side in previous:
                    line(*previous[side], *here, colors["text"], 4, dashed)
                previous[side] = here
                marks.append((*here, side == "measured"))
        put(plot_left + plot_width / 2, base + 14 + pitch, "queue depth", "body", "Regular",
            colors["muted"], plot_width, "center")
    rows_used = (len(panels) + columns - 1) // columns
    y += rows_used * panel_height + (rows_used - 1) * 56 + 48
    y += put(margin, y, FOOTER, "footer", "SemiBold", colors["muted"], inner)
    figure_height = int(-(-(y + space["footer_bottom"]) // 1))

    fonts = {face: FontProperties(fname=str(path)) for face, path in files.items()}
    to_points = 72 / 100
    with rc_context({"svg.fonttype": "path", "svg.hashsalt": "timing-queue-depth-obscur-v1"}):
        fig = Figure(figsize=((width + 0.01) / 100, (figure_height + 0.01) / 100), dpi=100,
                     facecolor=colors["canvas"])
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, width)
        ax.set_ylim(figure_height, 0)
        ax.axis("off")
        for x0, y0, x1, y1, color, stroke, dashed in lines:
            ax.add_line(Line2D([x0, x1], [y0, y1], color=color, linewidth=stroke * to_points,
                               linestyle=(0, (3, 2)) if dashed else "solid", solid_capstyle="butt"))
        for x, centre_y, filled in marks:
            ax.add_patch(Circle((x, centre_y), 10, facecolor=colors["text" if filled else "canvas"],
                                edgecolor=colors["text"], linewidth=3 * to_points, zorder=3))
        painted = []
        for x, label_y, text, face, size, color, align in labels:
            painted.append(ax.text(x, label_y, text, va="top", ha=align, fontproperties=fonts[face],
                                   fontsize=size, color=color, zorder=4))
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        inverse = ax.transData.inverted()
        boxes = []
        for label in painted:
            box = inverse.transform_bbox(label.get_window_extent(renderer))
            top, bottom = sorted((box.y0, box.y1))
            if box.x0 < 0 or top < 0 or box.x1 > width or bottom > figure_height:
                raise ValueError("A figure label extends outside the canvas: " + label.get_text())
            for other, x0, other_top, x1, other_bottom in boxes:
                if box.x0 < x1 and x0 < box.x1 and top < other_bottom - 4 and other_top < bottom - 4:
                    raise ValueError(f"Two figure labels overlap: {other} and {label.get_text()}")
            boxes.append((label.get_text(), box.x0, top, box.x1, bottom))
        for extension in ("png", "svg"):
            metadata = {"Description": description}
            if extension == "svg":
                metadata["Date"] = None
            path = output / f"{stem}.{extension}"
            fig.savefig(path, dpi=100, metadata=metadata)
            if extension == "svg":
                path.write_text("\n".join(row.rstrip() for row in path.read_text().splitlines()) + "\n",
                                encoding="utf-8", newline="\n")
        fig.clear()
    return {"width": width, "height": figure_height, "labels_within_canvas": len(labels),
            "minimum_font_points": min(item[4] for item in labels)}


def check_outputs():
    """Check the committed files with only the standard library."""
    record = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
    if record["evidence"] != semantic_record(load_data()):
        raise ValueError("Queue depth figure evidence is stale")
    for name in INPUTS:
        if record["inputs_sha256_lf"][name] != digest(ROOT / name, text=True):
            raise ValueError(f"Queue depth figure input changed: {name}")
    if set(record["outputs_sha256"]) != set(OUTPUTS):
        raise ValueError("Both PNG and SVG editions are required")
    for name, checksum in record["outputs_sha256"].items():
        if digest(ROOT / name) != checksum:
            raise ValueError(f"Queue depth figure output changed: {name}")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--font-dir", type=Path, help="Directory of the three official static Inter TTFs")
    parser.add_argument("--check", action="store_true", help="Verify committed files without fonts or rendering")
    args = parser.parse_args()
    if args.check:
        check_outputs()
        print("Both queue depth editions match the saved runs and renderer")
        return
    if args.font_dir is None:
        parser.error("--font-dir is required for authoring; no font download is performed")
    panels = load_data()
    files, font_evidence = font_files(args.font_dir)
    description = json.dumps(semantic_record(panels), sort_keys=True)
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        layout = {}
        for edition, wide in (("narrow", False), ("wide", True)):
            stem = "gpu-queue-depth-obscur" + ("-wide" if wide else "")
            layout[edition] = draw(panels, wide, files, output, stem, description)
        import PIL
        import matplotlib

        record = {
            "schema_version": 1,
            "evidence": semantic_record(panels),
            "rules": tokens()["basis"],
            "renderer": {"matplotlib": matplotlib.__version__, "Pillow": PIL.__version__, "backend": "Agg"},
            "layout": layout,
            "typography": {"release": "Inter 4.1", "files": font_evidence, "painted_faces": list(FACES)},
            "inputs_sha256_lf": {name: digest(ROOT / name, text=True) for name in INPUTS},
            "outputs_sha256": {"docs/" + path.name: digest(path) for path in sorted(output.iterdir())},
        }
        for path in output.iterdir():
            (ROOT / "docs" / path.name).write_bytes(path.read_bytes())
        (ROOT / MANIFEST).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    check_outputs()
    print("Rendered both queue depth editions from the saved runs with verified Inter files")


if __name__ == "__main__":
    main()
