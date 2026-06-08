# BUG-004: PDF export shows garbled text (literal &middot;, "bullet" markers, black boxes)

- **Severity:** High (PDF - the primary share format - looks broken)
- **Status:** Fixed
- **Reported:** 2026-06-08
- **Fixed:** 2026-06-08
- **Area:** `app/services/resume_text_renderer.py` (`render_pdf`, `_esc`, new `_pdf_safe`)
- **Introduced by:** ADR-066 (original PDF renderer)

## 1. What happened

The exported PDF resume was garbled in three visible ways (confirmed by rasterising the
real export):
- the contact line and flat skills list printed the literal text `&middot;`
  ("vishalsuthram12@gmail.com **&middot;** Atlanta, GA");
- every list item printed the literal word **`bullet`** as its marker
  ("ᵇᵘˡˡReduced risk by ..."); and
- characters outside the font's encoding (e.g. the non-breaking hyphen U+2011)
  rendered as a notdef **black box**.

md/txt/html/docx were unaffected.

## 2. Root cause

Three independent defects in `render_pdf`:
1. **Double-escaping.** The `P()` helper wraps text in `Paragraph(_esc(text))`, and
   `_esc` escapes `&` -> `&amp;`. The contact and flat-skills call sites pre-built a
   `&middot;` HTML entity, so `&` became `&amp;` and ReportLab rendered the literal
   `&middot;`.
2. **Wrong bullet API.** `ListItem(value="bullet")` sets the item's bullet text to the
   literal string `"bullet"`, overriding the `•` glyph from the `ListFlowable`.
3. **Font encoding.** ReportLab's standard Type-1 Helvetica is limited to WinAnsi
   (CP1252). Any codepoint outside it renders as notdef. LLM-authored resume text
   routinely contains a few (non-breaking hyphen, minus sign, arrows), and nothing
   normalised them before rendering.

## 3. Why it was not caught

The only PDF tests asserted the magic header (`payload[:5] == b"%PDF-"`) and that the
candidate's name appeared in the raw bytes. They never **extracted and inspected the
rendered text**, so literal `&middot;`, the `bullet` marker, and notdef boxes were all
invisible to the suite - the PDF "rendered" (valid bytes, name present) and passed.
Generating bytes is not the same as verifying glyphs; no test crossed that line. The
double-escape was also latent because the `&middot;` entity only appeared at two of the
many `P()` call sites.

## 4. Prevention

- **The fix:** contact/skills join with a literal middle-dot and pass RAW text to `P()`
  (escaped once); `ListItem`s drop `value="bullet"` so the `ListFlowable`'s `•` is used;
  and a new `_pdf_safe()` (called from `_esc` and the grouped-skills path) maps common
  non-CP1252 punctuation to ASCII and folds/drops anything still outside CP1252 so the
  PDF never emits a notdef box. CP1252 already covers smart quotes, dashes, middle dot,
  and accented Latin, so those survive. PDF-only; other formats are Unicode-native.
  ADR-091.
- **Forcing function:**
  `tests/v2/test_resume_text_renderer.py::test_render_pdf_text_has_no_literal_entities_or_bullet_word`
  extracts the rendered PDF text (PyMuPDF, `importorskip`) and asserts no `&middot;`,
  no `&amp;`, and no literal `bullet`; plus `::test_esc_single_escapes_and_does_not_double_escape`
  and `::test_pdf_safe_maps_non_winansi_punctuation_to_ascii` guard the helpers directly.
- **Generalization:** the text-extraction test catches the whole "PDF renders garbled
  text" class (entities, marker words, and - via the helper tests - notdef-prone glyphs),
  replacing the bytes-exist-only check that let this through. The principle generalises:
  for a rendered artifact, assert on the *rendered content*, not just that bytes were
  produced.
