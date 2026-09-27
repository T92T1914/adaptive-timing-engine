"""Offline presentation of supplied traces, independent of the timing executor."""

import hashlib
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
FACES = (
    (400, "normal", "Inter Regular", "Inter-Regular"),
    (400, "italic", "Inter Italic", "Inter-Italic"),
    (600, "normal", "Inter SemiBold", "Inter-SemiBold"),
    (600, "italic", "Inter SemiBold Italic", "Inter-SemiBoldItalic"),
    (700, "normal", "Inter Bold", "Inter-Bold"),
    (700, "italic", "Inter Bold Italic", "Inter-BoldItalic"),
)
FONT_STACK = '"Timing Inter", Inter, system-ui, "Segoe UI", sans-serif'
RESOURCES = ("presentation.py", "presentation.json", "appearance.js", "viewer.html")


def tokens():
    data = json.loads((ROOT / "presentation.json").read_text(encoding="utf-8"))
    for theme in data["themes"].values():
        if not all(re.fullmatch(r"#[0-9a-f]{6}", value) for value in theme.values()):
            raise ValueError("Appearance roles must be explicit RGB colors")
    return data


def appearance_css():
    data = tokens()
    faces = "\n".join(
        '@font-face{font-family:"Timing Inter";'
        f'src:local("{full}"),local("{postscript}");'
        f"font-weight:{weight};font-style:{style};font-display:swap}}"
        for weight, style, full, postscript in FACES
    )

    def declarations(mode):
        result = f"color-scheme:{'light' if mode == 'clair' else 'dark'};"
        result += "".join(f"--{role}:{value};" for role, value in data["themes"][mode].items())
        return result + "".join(
            f"--state-{name}:{state[mode]};" for name, state in data["states"].items()
        )

    light, dark = declarations("clair"), declarations("obscur")
    return (faces + f"\n:root{{{light}}}\n"
            + '@media(prefers-color-scheme:dark){:root:not([data-appearance="clair"])'
            + f"{{{dark}}}}}\n"
            + f':root[data-appearance="obscur"]{{{dark}}}\n'
            + f':root[data-appearance="clair"]{{{light}}}\n'
            + f"@media print{{:root,:root[data-appearance]{{{light}}}}}\n")


def appearance_control():
    return ('<div class="appearance-control"><label for="appearance">Appearance</label>'
            '<select id="appearance" disabled><option value="auto">Auto</option>'
            '<option value="clair">Clair</option><option value="obscur">Obscur</option>'
            '</select></div>')


def safe_json(value):
    return (json.dumps(value, separators=(",", ":"), allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def report_hash(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def renderer_hashes():
    # LF text content is stable across Windows and Linux checkouts.
    return {name: hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode()).hexdigest()
            for name in RESOURCES}


def summary(report):
    rows = []
    for case in report.get("cases", []):
        values = [case.get(key, "not recorded") for key in
                  ("scenario", "variant", "seed", "events", "planned", "rejected", "constrained")]
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in values) + "</tr>")
    return ('<p>JavaScript is disabled. Auto follows your system appearance. The saved '
            'case summaries are below. The full report remains embedded in this file.</p>'
            '<div class="tablewrap" tabindex="0" role="region" aria-label="Saved case summaries">'
            '<table><caption>Saved cases, without a new evaluation</caption><thead><tr>'
            '<th>Workload</th><th>Policy</th><th>Seed</th><th>Tasks</th><th>Planned</th>'
            '<th>Rejected</th><th>Constrained</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>')


def write_viewer(report: dict, output: Path, *, provenance=None) -> dict:
    """Render the supplied values without running a workload or timing worker."""
    identity = dict(provenance or {})
    identity.update(report_sha256=report_hash(report), renderer_lf_sha256=renderer_hashes(),
                    theme_source=tokens()["source"])
    template = (ROOT / "viewer.html").read_text(encoding="utf-8")
    replacements = {
        "__APPEARANCE_SCRIPT__": (ROOT / "appearance.js").read_text(encoding="utf-8"),
        "__APPEARANCE_CSS__": appearance_css(), "__APPEARANCE_CONTROL__": appearance_control(),
        "__FONT_STACK__": FONT_STACK, "__FONT_STACK_JSON__": json.dumps(FONT_STACK),
        "__NO_SCRIPT_SUMMARY__": summary(report),
        "__PRESENTATION_JSON__": safe_json(identity),
        "__PRESENTATION_TEXT__": html.escape(json.dumps(identity, indent=2)),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(template.replace("__REPORT_DATA__", safe_json(report)), encoding="utf-8", newline="\n")
    return identity
