"""Render a compact Obscur sharing summary from maintained evidence, without an experiment."""

import argparse
import hashlib
import importlib
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONFIG_PATH = ROOT / "docs/sharing-card.json"
RECEIPT = ROOT / "docs/sharing-card-receipt.json"
SIZE = (1280, 640)
MARGIN = 72


def digest(path):
    data = path.read_bytes()
    if path.suffix in (".py", ".json", ".md", ".css"):
        data = path.read_text(encoding="utf-8").encode()
    return hashlib.sha256(data).hexdigest()


def content(config):
    source = ROOT / config["source"]
    if config["kind"] != "timing":
        raise ValueError("This repository shares only the timing summary")
    data = importlib.import_module("tools." + config["helper"]).load_data()
    if data["stats"]["replaced"] != 99 or data["stats"]["stale"] != 1:
        raise ValueError("Retained worker result changed")
    return dict(
        title=config["title"],
        subtitle=config["subtitle"],
        panels=[
            ["Requests", str(data["request_count"])],
            ["Calculations", str(data["stats"]["computed"])],
        ],
        footer=[
            "99 requests replaced. 1 result rejected.",
            "Controlled worker example, not a benchmark.",
        ],
        source_sha256=digest(source),
    )


def inputs(config):
    names = [
        "tools/render_share_preview.py",
        "tools/obscur_figure.py",
        "docs/sharing-card.json",
        config["source"],
        config["tokens"],
        "tools/" + config["helper"] + ".py",
    ]
    return {name: digest(ROOT / name) for name in names}


def check():
    config = json.loads(CONFIG_PATH.read_text())
    receipt = json.loads(RECEIPT.read_text())
    if receipt["inputs_sha256_lf"] != inputs(config) or receipt["content"] != content(
        config
    ):
        raise ValueError("Sharing evidence or renderer changed")
    data = (ROOT / config["output"]).read_bytes()
    if (
        hashlib.sha256(data).hexdigest() != receipt["png_sha256"]
        or data[:8] != b"\x89PNG\r\n\x1a\n"
        or struct.unpack(">II", data[16:24]) != SIZE
        or data[25] != 2
        or len(data) >= 1_000_000
    ):
        raise ValueError("Sharing PNG identity, dimensions, opacity or size differs")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check()
        print("Sharing PNG matches retained content and renderer")
        return
    if args.font_dir is None:
        parser.error("--font-dir is required, no font is downloaded")
    from PIL import Image, ImageDraw, ImageFont, __version__

    from tools.obscur_figure import font_files, pixels, tokens

    config = json.loads(CONFIG_PATH.read_text())
    rules = tokens()
    palette, space = rules["colors"], rules["space"]
    points = rules["type"]["points"]["wide"]
    data = content(config)
    files, faces = font_files(args.font_dir)
    image = Image.new("RGB", SIZE, palette["canvas"])
    draw = ImageDraw.Draw(image)
    labels = []

    def label(x, y, text, role, face, color, limit=SIZE[0] - 2 * MARGIN, card=None):
        size = pixels(points[role])
        font = ImageFont.truetype(str(files[face]), size)
        bounds = draw.textbbox((x, y), text, font=font, anchor="la")
        edge = space["label_clearance"]
        inside = card or (0, 0, SIZE[0], SIZE[1])
        pad = edge if card else 0
        if (
            bounds[0] < inside[0] + pad
            or bounds[1] < inside[1] + pad
            or bounds[2] > inside[2] - pad
            or bounds[3] > inside[3] - pad
            or bounds[2] - bounds[0] > limit
        ):
            raise ValueError("Sharing label leaves its safe region: " + text)
        draw.text((x, y), text, font=font, fill=color, anchor="la")
        labels.append(
            dict(text=text, face="Inter-" + face, bounds=list(bounds),
                 points=points[role])
        )
        return size * rules["type"]["line_spacing"]

    y = 48
    y += label(MARGIN, y, data["title"], "title", "Bold", palette["text"]) + 14
    y += label(MARGIN, y, data["subtitle"], "subtitle", "Regular", palette["muted"])
    top = round(y + 36)
    card_width, pad = 500, space["card_pad_side"]
    heights = []
    for index, (name, value) in enumerate(data["panels"]):
        x = MARGIN + index * (card_width + 78)
        name_height = pixels(points["card_title"]) * rules["type"]["line_spacing"]
        value_height = pixels(points["title"]) * rules["type"]["line_spacing"]
        bottom = round(
            top + space["card_pad_top"] + name_height + space["title_body_gap"]
            + value_height + space["card_pad_bottom"]
        )
        box = (x, top, x + card_width, bottom)
        draw.rounded_rectangle(
            box, radius=space["card_radius"], fill=palette["control"],
            outline=palette["border"], width=space["outline"],
        )
        cursor = top + space["card_pad_top"]
        cursor += label(x + pad, cursor, name, "card_title", "SemiBold",
                        palette["text"], card_width - 2 * pad, box)
        cursor += space["title_body_gap"]
        label(x + pad, cursor, value, "title", "Bold", palette["text"],
              card_width - 2 * pad, box)
        heights.append(bottom)
    y = max(heights) + 40
    for number, line in enumerate(data["footer"]):
        if number:
            y += space["body_line_gap"]
        y += label(MARGIN, y, line, "footer", "SemiBold", palette["muted"])
    output = ROOT / config["output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)
    receipt = dict(
        schema_version=2,
        content=data,
        inputs_sha256_lf=inputs(config),
        png_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        dimensions=list(SIZE),
        appearance="obscur",
        opaque=True,
        rules=rules["basis"],
        renderer={"Pillow": __version__},
        font_release=rules["type"]["release"],
        font_files=faces,
        painted_labels=labels,
        experimental_work_rerun=False,
        kind="authored summary, not an application screenshot",
    )
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    check()
    print("Rendered opaque Inter sharing summary from retained evidence")


if __name__ == "__main__":
    main()
