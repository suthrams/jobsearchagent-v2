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
3:1 to 5:1 ratio), `title`, `layout`, optional `footer`
(list of strings).

Layouts:
- `ranked_rows` - `subtitle`, `rows[]` of
  `{label, meta, value, weight, focal?}`. Bar length = weight/max.
- `bars` - `bars[]` of `{name, text, weight, kind:"slow"|"fast"}`,
  optional `note`. For proportional comparison (e.g. 75 vs 20).
- `flow` - `flow[]` left-to-right: a node `{title, meta[], accent?,
  focal?}`, an arrow `{arrow:true}` or `{arrow:"label"}`, or a stack
  `{title, stack:[nodes]}`.
- `tiers` - `left`/`right` each `{head, items[nodes]}`, plus
  `connector` (dashed label between them).

Accents: `green` cheap/safe, `magenta` premium/strong, `blue` outcome,
`red` blocked/failure, `focal:true` the single amber emphasis.

## Adding a figure

1. Write `specs/diag_v2_articleN_NN_name.json` with the exact published
   text. 2. `python tools/render_figures.py articleN_NN`. 3. Send the
   PNG back for verification. 4. Wire into the article.

## v1 note

The renderer is built but cannot be render-tested in the agent sandbox
(no Chromium there). First real run is a look-validation: render
`article6_05` first, share it back, the theme gets tuned from the
actual output, then it is locked for the whole series.
