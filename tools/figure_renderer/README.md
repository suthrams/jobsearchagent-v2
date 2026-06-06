# Figure renderer (deterministic, series-grade)

Produces the rich dark/operational article visuals WITHOUT the
text-fidelity lottery. Every label and number is literal HTML from a
JSON spec, so it is always exact and ASCII, and the aspect ratio is
locked. This is the series default for figures (banners may still use an
image model; data figures must use this).

## Run (locally; needs the Chromium you already have for Playwright)

    python -m playwright install chromium       # once
    python tools/render_figures.py              # all -> _rendered/
    python tools/render_figures.py article6_05   # one (substring)
    python tools/render_figures.py article6_05 --promote

NON-DESTRUCTIVE by default: renders to `blogs/blog_images/_rendered/
<id>.png` and never touches the published images. Compare it against
the live `blogs/blog_images/<id>.png`, send the rendered one back for
the number-by-number verification gate, then re-run with `--promote`
to copy it over the canonical name (the current image is backed up to
`<id>__prev_backup.png` first). Rendered at 2x for crispness.

## Files

- `theme.css`     - the house style. Edit here to tune the look once;
                     every figure and every future article inherits it.
- `template.html` - shell + a small vanilla-JS layout engine.
- `render_figures.py` (in tools/) - Playwright driver: spec -> PNG.
- `specs/*.json`  - one spec per figure. Text here is the published
                     text, verbatim. This is the fidelity guarantee:
                     numbers are data, never redrawn by a model.

## Spec schema

Common keys: `id` (output filename stem), `width`, `height` (pick a
3:1 to 5:1 ratio; tables/compares run taller), `title`, `layout`,
optional `headSub`, optional `footer` (list of strings or
`{main, sub}`), optional `takeaway` (see below).

**Title / takeaway highlight.** Any title, takeaway, label, or cell text
may color one keyword with `{{accent:word}}` tokens, e.g.
`"Never trust the {{green:green}} dashboard."` Text stays literal + ASCII
in the spec; only the marked span is colored. This is how the lesson line
gets its emphasis (series guidelines Section 8).

**Takeaway band** (`takeaway`): `{icon?, accent?, text}`. A prominent
bottom band carrying the one-line lesson. ALWAYS rendered white + large
(legibility invariant); never put the lesson in a muted color.

Layouts:
- `ranked_rows` - `subtitle`, `rows[]` of
  `{label, meta, value, weight, focal?}`. Bar length = weight/max.
- `bars` - `bars[]` of `{name, text, weight, kind:"slow"|"fast"}`,
  optional `note`. For proportional comparison (e.g. 75 vs 20).
- `flow` - `flow[]` left-to-right: a node `{title, meta[], accent?,
  focal?, icon?}`, an arrow `{arrow:true}` or `{arrow:"label"}` (label
  renders as an overlap-proof chip), or a stack `{title, stack:[nodes]}`.
- `tiers` - `left`/`right` each `{head, items[nodes]}`, plus
  `connector` (dashed label between them).
- `table` - icon-rail rows (what / why / how / when). `head[]` column
  titles, optional `gridCols` (CSS grid-template-columns), `rows[]` of
  `{icon?, accent?, focal?, label, sub?, cells[]}`. A cell is a string or
  `{text?, code?, sub?, num?}` (`code` renders an inline monospace chip,
  `num` right-weights a number).
- `compare` - two panels (KEEP/DROP, BEFORE/AFTER). `left`/`right` each
  `{head, tone:"good"|"bad", rows[] of {icon?, label, sub?}}`, optional
  `mid` `{tag?, lines[]}` center connector.
- `lanes` - stacked swimlanes. `lanes[]` each `{title, tone:"good"|"blue",
  sub?, direction?:"down", flow[]}` (flow = same node/arrow grammar as the
  `flow` layout), or `{transition:"LABEL"}` for a between-lane caption.
- `cards` - metric cards (dashboards/banners). `cards[]` of
  `{icon?, accent?, label, value, sub?}`.
- `scene` - bespoke SVG node-graph for CUSTOM topologies (radial maps,
  before/after graphs) that no preset layout fits. `scene` =
  `{viewBox:[x,y,w,h], nodes[], edges[], zones[], labels[]}`:
  - `nodes[]`: `{id, x, y, w, h, title, sub?, icon?, accent?, focal?}`
    (coords in viewBox space; icon drawn top-center, title + sub below).
  - `edges[]`: `{from, to, accent?, style?:"thin"|"thick", dashed?, bend?,
    label?, labelDx?, labelDy?}`. `from`/`to` are either `[x,y]` points or
    a node anchor `"id:side"` (`side` = left|right|top|bottom|center).
    `bend` curves the edge; labels render as overlap-proof chips.
  - `zones[]`: `{x, y, w, h, accent?, dashed?, fill?:false, label?}` -
    grouping rectangles drawn underneath (e.g. an amber dashed "zoom"
    frame). - `labels[]`: free text `{x, y, text, accent?, size?, tag?,
    anchor?, weight?}` (`tag:true` = uppercase letter-spaced caption).
  First real use: `diag_v2_article12_01_api_roles` (the three-role API map).

Accents: `green` cheap/safe/kept, `magenta` premium/strong, `blue`
outcome/source, `red` blocked/failure/dropped, `focal:true` the single
amber emphasis.

**Icons** (`icon` on nodes/rows/cards/takeaway). Inline SVG, tinted by the
row's accent. Available names: `cost activity globe steps shield user gauge
chart eye eyeoff check doc robot database code filter table edit target
flask alert arrow info lock clock pen`. Add more in `template.html` ICONS.

## Adding a figure

1. Write `specs/diag_v2_articleN_NN_name.json` with the exact published
   text. 2. `python tools/render_figures.py articleN_NN`. 3. Send the
   PNG back for verification. 4. Wire into the article.

## v1 note

The renderer is built but cannot be render-tested in the agent sandbox
(no Chromium there). First real run is a look-validation: render
`article6_05` first, share it back, the theme gets tuned from the
actual output, then it is locked for the whole series.
