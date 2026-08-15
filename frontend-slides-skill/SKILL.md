---
name: frontend-slides
description: Build presentation slide decks as standalone, viewport-scaled HTML/CSS/JS pages instead of PowerPoint files. Use when the user wants a "slide deck", "pitch deck", or "presentation" delivered as a web page, wants an animated or interactive deck, wants to restyle/rebuild an existing .pptx as HTML, or asks to export HTML slides to PDF or deploy them somewhere. Covers the fixed-canvas/scale-to-fit layout convention, style presets, animation patterns, a ready-made "Bold" template pack, and scripts to pull content out of .pptx files, export a deck to PDF, and deploy it.
---

# Frontend Slides

Build presentation decks as a single self-contained HTML file (or small
folder) instead of a `.pptx`. Every slide is a fixed-size canvas that scales
to fit whatever screen or PDF page it's rendered on, so decks look identical
in a browser tab, projected full-screen, or printed.

## When to use this

- The user asks for a "slide deck", "pitch deck", "presentation", or
  "one-pager deck" and a web page is an acceptable (or preferred) delivery
  format.
- The user wants a deck with motion — animated reveals, transitions between
  slides — that PowerPoint can't do well.
- The user has an existing `.pptx` and wants it rebuilt, restyled, or
  extended as HTML.
- The user asks to export a deck to PDF or publish/deploy it somewhere.

If the user explicitly needs a real `.pptx` file (to hand off for editing in
PowerPoint/Keynote, or because their audience requires it), use the `pptx`
skill instead.

## Workflow

1. **Gather content.** If there's an existing `.pptx` to build from, run
   `scripts/extract-pptx.py` to pull its text, speaker notes, and images into
   a structured outline. Otherwise, draft the outline directly from the
   user's brief: one heading per slide, the key points, any stats or quotes.
2. **Pick a style.** Read `STYLE_PRESETS.md` and choose (or ask the user to
   choose) a preset. `bold-template-pack/` is a fully worked example of the
   Bold preset — copy it as a starting point rather than writing CSS from
   scratch.
3. **Build the deck.** Follow `html-template.md` for the document skeleton:
   one `index.html`, one `deck.css` for preset overrides, `viewport-base.css`
   included unmodified. Each slide is a `<section class="slide">`.
4. **Add motion (optional).** Use `animation-patterns.md` for entrance
   reveals, staggered lists, and between-slide transitions. Keep it subtle —
   motion should clarify hierarchy, not distract from it.
5. **Preview.** Open `index.html` in a browser (or serve it locally) and
   click/arrow through every slide at a few window sizes before calling it
   done.
6. **Ship it.** Use `scripts/export-pdf.sh` for a static PDF handout, and/or
   `scripts/deploy.sh` to publish the deck as a web page.

## File map

| File | Purpose |
|---|---|
| `SKILL.md` | This file — workflow and conventions. |
| `viewport-base.css` | Base stylesheet every deck includes unmodified: canvas sizing, scale-to-fit, print layout, typography scale, layout utilities. |
| `html-template.md` | The `index.html` skeleton to copy for a new deck, with the scaling/navigation script. |
| `STYLE_PRESETS.md` | Catalog of style presets (palette, type, spacing philosophy, when to use each). |
| `bold-template-pack/` | Complete worked example of the Bold preset — CSS + a sample deck with the common slide types filled in. |
| `animation-patterns.md` | Copy-paste CSS/JS for entrance animations, staggered reveals, and slide-to-slide transitions. |
| `scripts/extract-pptx.py` | Pulls text, notes, and images out of an existing `.pptx` into a Markdown/JSON outline. |
| `scripts/export-pdf.sh` | Renders a deck's `index.html` to a paginated PDF via headless Chromium. |
| `scripts/deploy.sh` | Publishes a deck folder: local preview server, plain static copy, or GitHub Pages. |

## Canvas convention

Every slide is a fixed **1920×1080** canvas (16:9). Never use viewport units
(`vw`/`vh`) or percentages for the slide itself — lay it out in absolute
pixels against that 1920×1080 frame, exactly as you would in a design tool.
`viewport-base.css` handles scaling that fixed canvas down (or up) to fit the
real window or PDF page with a single `transform: scale(...)`, computed by
the small script in `html-template.md`. This is what keeps text, spacing,
and images from reflowing differently on every screen — the deck is always
laid out once, at one size, and then uniformly scaled.

Consequences of this convention:
- Font sizes, gaps, and image dimensions are all fixed px values tuned for
  the 1920×1080 frame — don't make them responsive.
- Never let slide content depend on the actual browser window size; only the
  outer scale wrapper should respond to it.
- Keep a consistent safe margin (96px is the default in `viewport-base.css`)
  so content doesn't get cropped on projectors that overscan slightly.

## Checklist before calling a deck done

- [ ] Every slide fits inside the 1920×1080 frame with no overflow/clipping.
- [ ] Text is legible at the "shrunk to fit a laptop window" scale, not just
      at native size.
- [ ] Keyboard/click navigation between slides works (see `html-template.md`).
- [ ] Animations respect `prefers-reduced-motion` (see `animation-patterns.md`).
- [ ] `scripts/export-pdf.sh` produces one clean page per slide with no
      cut-off content.
- [ ] Colors and type come from the chosen preset, not ad hoc choices per
      slide.
