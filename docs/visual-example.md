# Read the visual example

<a href="visual-example-data.json">
  <picture>
    <source media="(min-width: 768px) and (prefers-color-scheme: dark)" srcset="adaptive-timing-obscur-wide.png">
    <source media="(min-width: 768px) and (prefers-color-scheme: light)" srcset="adaptive-timing-clair-wide.png">
    <source media="(prefers-color-scheme: dark)" srcset="adaptive-timing-obscur.png">
    <source media="(prefers-color-scheme: light)" srcset="adaptive-timing-clair.png">
    <img src="adaptive-timing-clair.png" alt="Recorded worker example: 101 submitted requests produce two calculations, 99 waiting requests are replaced and one stale result is rejected. The retained result is payload 100, revision 101. Spacing shows event order, not elapsed time." width="900">
  </picture>
</a>

One calculation can run while one request waits. New requests replace the waiting request; an obsolete result is rejected when the active calculation finishes. The diagram shows event order, not elapsed time.

## Reproduce the values

Run from this repository using its documented Python environment.
Evidence was checked against commit `6e12229`.

```python
from adaptive_timing.experiment import worker_experiment

result = worker_experiment()
print(result["request_count"])
print(result["computed_payloads"])
print(result["stats"])
```

The first calculation is deliberately held with a thread event. Payloads 1
through 100 arrive while it is active. The waiting slot is replaced 99 times,
and only payloads 0 and 100 are calculated. Payload 0's obsolete result is
rejected. Payload numbers begin at zero; request revisions begin at one.
The current retained result is payload 100, revision 101.

The experiment does not establish a speedup, a CPU contention limit, or a
guarantee about operating system scheduling. The separate [timing policy
experiments](evidence/results.md) use synthetic workloads and are not a
validated model of human performance.

## Inspect the source

The [underlying values](visual-example-data.json) include the source and
conditions. The new [Clair SVG](adaptive-timing-clair.svg) and [Obscur SVG](adaptive-timing-obscur.svg) carry outlined Inter labels. The [original PNG](adaptive-timing-example.png) and [original SVG](adaptive-timing-example.svg) retain their recorded bytes.
The figure is a visual explanation of the public implementation, not a
screenshot of an external application.

## Rebuild the figure without another experiment

The maintained renderer reads the saved counters. It does not run the worker,
planner or timing experiment. Supply the six official static Inter TTF files
from one release in a local directory:

```sh
python -m pip install -r requirements-figures.txt
python tools/render_worker_figure.py --font-dir /path/to/Inter/extras/ttf
python tools/render_worker_figure.py --check
```

Each font's name, weight, italic flag and Latin glyph coverage are checked.
Regular, Semibold, Bold and genuine Italic supply this diagram's labels. The
[rendering receipt](adaptive-timing-figure.json) records all six input hashes,
retained values, tokens and output identities. No font files are distributed or
downloaded. PNGs rasterize the intended Inter glyphs. SVGs outline those same
labels, with selectable explanations and JSON available here.

The figure follows the site's effective Auto, Clair or Obscur choice. GitHub
uses light/dark picture sources and a Clair fallback. Print uses Clair. The
compact vertical sequence keeps labels readable inside a narrow README.
Rendering checks reject cropped labels, and site builds verify the committed
images without font dependencies or a silent rebuild.

The source string calls the hundred later arrivals replacements. More precisely,
the first arrival occupies the empty waiting slot and the next 99 replace it.
The stored counters and source JSON are unchanged. Source `6e12229` belongs to
this controlled worker example. It does not establish the unknown evaluated
revision of the separate 48 case trace report.

## Wide and narrow columns

The same renderer also makes a wide composition from the same retained values.
The website chooses its layout from the figure container at 560 pixels and keeps
its existing Auto, Clair and Obscur appearance setting. The README uses a 1024
pixel viewport breakpoint, while these notes use 768 pixels to account for their
wider reading column. These choices follow measurements of the actual GitHub
columns, not an assumption that viewport width equals image width. Signed-out
system appearance is covered. Signed-in appearance overrides remain unverified.

Wide [Clair SVG](adaptive-timing-clair-wide.svg) and [Obscur SVG](adaptive-timing-obscur-wide.svg) preserve the original units, qualifications and Inter outlines. Narrow editions remain available above.
