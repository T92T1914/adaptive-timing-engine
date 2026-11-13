"""Draw Obscur figures from measured text, using the rules in tools/obscur-figure.json."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS_PATH = "tools/obscur-figure.json"
FACES = {"Regular": (400, False), "SemiBold": (600, False), "Bold": (700, False)}


def digest(path, text=False):
    data = path.read_text(encoding="utf-8").encode() if text else path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def tokens():
    return json.loads((ROOT / TOKENS_PATH).read_text(encoding="utf-8"))


def font_files(directory):
    """Require the static Inter faces from one release, with no fallback or download."""
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


def pixels(points):
    return points * 100 / 72


class Measure:
    """Measure and wrap text by the width of the actual font file."""

    def __init__(self, files):
        self.files, self.cache = files, {}

    def width(self, text, face, size):
        from PIL import ImageFont

        key = (face, size)
        if key not in self.cache:
            self.cache[key] = ImageFont.truetype(str(self.files[face]), size)
        return self.cache[key].getlength(text)

    def wrap(self, text, face, size, limit):
        lines, current = [], ""
        for word in text.split():
            trial = (current + " " + word).strip()
            if self.width(trial, face, size) <= limit:
                current = trial
            elif not current:
                raise ValueError(f"A word does not fit, change the layout: {word}")
            else:
                lines.append(current)
                current = word
        if self.width(current, face, size) > limit:
            raise ValueError(f"A word does not fit, change the layout: {current}")
        return lines + [current]


def layout(spec, wide, files):
    """Place every label from measured text. Spec keys: title, subtitle, stats, cards, footer."""
    rules = tokens()
    space = rules["space"]
    edition = space["wide" if wide else "narrow"]
    points = rules["type"]["points"]["wide" if wide else "narrow"]
    colors = rules["colors"]
    pitch_scale = rules["type"]["line_spacing"]
    measure = Measure(files)
    width, margin = edition["width"], edition["margin"]
    inner = width - 2 * margin
    texts = []

    def put(x, y, text, role, face, color, limit, card=None):
        size = pixels(points[role])
        lines = measure.wrap(text, face, size, limit)
        pitch = size * pitch_scale
        for index, line in enumerate(lines):
            texts.append(
                dict(x=x, y=y + index * pitch, text=line, face=face,
                     points=points[role], color=color, card=card)
            )
        return len(lines) * pitch

    y = margin
    y += put(margin, y, spec["title"], "title", "Bold", colors["text"], inner) + 14
    y += put(margin, y, spec["subtitle"], "subtitle", "Regular", colors["muted"], inner)
    y += 44
    stats = spec.get("stats", [])
    if stats:
        step = edition["card_width"] + edition["gap"]
        stat_top, stat_bottom = y, y
        for index, (number, unit) in enumerate(stats):
            x = margin + (index * step if wide else 0)
            top = stat_top if wide else y
            end = top + put(x, top, number, "title", "Bold", colors["text"],
                            edition["card_width"])
            end += put(x, end, unit, "body", "Regular", colors["text"],
                       edition["card_width"])
            stat_bottom = max(stat_bottom, end)
            if not wide:
                y = end + space["body_line_gap"]
        y = (stat_bottom if wide else y) + 40
    pad = space["card_pad_side"]
    text_limit = edition["card_width"] - 2 * pad
    sized = []
    for number, card in enumerate(spec["cards"], 1):
        size = pixels(points["card_title"])
        head = measure.wrap(f"{number}. {card['title']}", "SemiBold", size, text_limit)
        height = space["card_pad_top"] + len(head) * size * pitch_scale
        height += space["title_body_gap"]
        body = []
        for number, line in enumerate(card["lines"]):
            size = pixels(points["body"])
            wrapped = measure.wrap(line, "Regular", size, text_limit)
            body.append(len(wrapped) * size * pitch_scale)
            height += body[-1] + (space["body_line_gap"] if number else 0)
        height += space["card_pad_bottom"]
        sized.append(height)
    columns = edition["columns"]
    cards, row_top = [], y
    for start in range(0, len(spec["cards"]), columns):
        row = range(start, min(start + columns, len(spec["cards"])))
        row_height = max(sized[i] for i in row)
        for i in row:
            x = margin + (i - start) * (edition["card_width"] + edition["gap"])
            card = spec["cards"][i]
            rect = dict(id=card["id"], x=x, y=row_top, w=edition["card_width"],
                        h=row_height, mark=card.get("mark", "plain"))
            cards.append(rect)
            cursor = row_top + space["card_pad_top"]
            cursor += put(x + pad, cursor, f"{i + 1}. {card['title']}", "card_title",
                          "SemiBold", colors["text"], text_limit, card=len(cards) - 1)
            cursor += space["title_body_gap"]
            for number, line in enumerate(card["lines"]):
                if number:
                    cursor += space["body_line_gap"]
                cursor += put(x + pad, cursor, line, "body", "Regular",
                              colors["text"], text_limit, card=len(cards) - 1)
        row_top += row_height + edition["gap"]
    y = row_top - edition["gap"] + 48
    for number, line in enumerate(spec["footer"]):
        if number:
            y += space["body_line_gap"]
        y += put(margin, y, line, "footer", "SemiBold", colors["muted"], inner)
    height = int(-(-(y + space["footer_bottom"]) // 1))
    return dict(width=width, height=height, texts=texts, cards=cards, columns=columns,
                margin=margin, edition=edition)


def render(spec, wide, files, output, stem, salt, description):
    """Draw one edition as PNG and SVG. Reject a label outside its card or the canvas."""
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import rc_context
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
    from matplotlib.path import Path as MPath

    rules = tokens()
    colors, space = rules["colors"], rules["space"]
    plan = layout(spec, wide, files)
    width, height = plan["width"], plan["height"]
    fonts = {name: FontProperties(fname=str(path)) for name, path in files.items()}
    outline = space["outline"] * 72 / 100
    stroke = space["connector"] * 72 / 100
    with rc_context({"svg.fonttype": "path", "svg.hashsalt": salt}):
        fig = Figure(figsize=((width + 0.01) / 100, (height + 0.01) / 100), dpi=100,
                     facecolor=colors["canvas"])
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        for card in plan["cards"]:
            mark = card["mark"]
            ax.add_patch(FancyBboxPatch(
                (card["x"], card["y"]), card["w"], card["h"],
                boxstyle=f"round,pad=0,rounding_size={space['card_radius']}",
                facecolor=colors["selected" if mark == "current" else "control"],
                edgecolor=colors["border"], linewidth=outline,
                linestyle=(0, (5, 3)) if mark == "dashed" else "solid",
                gid=f"event-{card['id']}"))
            if mark == "current":
                ax.add_patch(FancyBboxPatch(
                    (card["x"] + 7, card["y"] + 7), card["w"] - 14, card["h"] - 14,
                    boxstyle=f"round,pad=0,rounding_size={space['card_radius'] - 7}",
                    facecolor="none", edgecolor=colors["focus"], linewidth=outline,
                    gid=f"focus-{card['id']}"))
        cards = plan["cards"]
        for index in range(len(cards) - 1):
            a, b = cards[index], cards[index + 1]
            same_row = a["y"] == b["y"]
            if same_row:
                y = a["y"] + a["h"] / 2
                points = [(a["x"] + a["w"] + 6, y), (b["x"] - 6, y)]
            elif plan["columns"] == 1:
                x = a["x"] + a["w"] / 2
                points = [(x, a["y"] + a["h"] + 3), (x, b["y"] - 3)]
            else:
                mid = a["y"] + a["h"] + (b["y"] - a["y"] - a["h"]) / 2
                ax_x, bx_x = a["x"] + a["w"] / 2, b["x"] + b["w"] / 2
                points = [(ax_x, a["y"] + a["h"] + 6), (ax_x, mid), (bx_x, mid),
                          (bx_x, b["y"] - 6)]
            path = MPath(points, [MPath.MOVETO] + [MPath.LINETO] * (len(points) - 1))
            ax.add_patch(FancyArrowPatch(
                path=path, arrowstyle="-|>", mutation_scale=22, linewidth=stroke,
                color=colors["text"], shrinkA=0, shrinkB=0, gid=f"next-{a['id']}"))
        labels = []
        for item in plan["texts"]:
            label = ax.text(item["x"], item["y"], item["text"], va="top", ha="left",
                            fontproperties=fonts[item["face"]], fontsize=item["points"],
                            color=item["color"])
            labels.append((label, item))
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer, clearance = canvas.get_renderer(), space["label_clearance"]
        inverse = ax.transData.inverted()
        inside_cards = 0
        for label, item in labels:
            box = inverse.transform_bbox(label.get_window_extent(renderer))
            top, bottom = min(box.y0, box.y1), max(box.y0, box.y1)
            if box.x0 < 0 or top < 0 or box.x1 > width or bottom > height:
                raise ValueError("A figure label extends outside the canvas: "
                                 + item["text"])
            if item["card"] is not None:
                card = plan["cards"][item["card"]]
                if (box.x0 < card["x"] + clearance
                        or box.x1 > card["x"] + card["w"] - clearance
                        or top < card["y"] + clearance
                        or bottom > card["y"] + card["h"] - clearance):
                    raise ValueError("A label is closer than 12 px to its card edge: "
                                     + item["text"])
                inside_cards += 1
        layout_record = {
            "width": width, "height": height, "cards": len(plan["cards"]),
            "labels_within_canvas": len(labels), "labels_within_cards": inside_cards,
            "minimum_font_points": min(item["points"] for _, item in labels),
        }
        for extension in ("png", "svg"):
            metadata = {"Description": description}
            if extension == "svg":
                metadata["Date"] = None
            path = output / f"{stem}.{extension}"
            fig.savefig(path, dpi=100, metadata=metadata)
            if extension == "png":
                header = path.read_bytes()[16:24]
                if (int.from_bytes(header[:4], "big"), int.from_bytes(header[4:], "big")) != (width, height):
                    raise ValueError("The PNG size differs from the planned layout")
            if extension == "svg":
                path.write_text(
                    "\n".join(line.rstrip() for line in path.read_text().splitlines())
                    + "\n", encoding="utf-8", newline="\n")
        fig.clear()
    return layout_record
