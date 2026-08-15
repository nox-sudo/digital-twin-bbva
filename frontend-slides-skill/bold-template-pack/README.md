# Bold template pack

A complete, working example of the Bold preset from `STYLE_PRESETS.md`.
Open `index.html` directly in a browser to preview it — arrow keys or click
left/right half of the screen to navigate.

## Using this as a starting point

1. Copy this folder (or just `bold.css`) into the new deck's directory.
2. Copy `index.html`'s structure into the deck's real `index.html`
   (`html-template.md` has the bare skeleton without example content) and
   replace the example slides with the deck's actual content.
3. Keep the `../viewport-base.css` link pointing at the shared base
   stylesheet — don't duplicate it into the deck folder.
4. Adjust `--accent` in `bold.css` (renamed to `deck.css` in the new deck)
   to the user's brand color; leave the rest of the tokens unless the user
   has specific brand fonts.

## What it demonstrates

- `slide-1` / `slide-6` — hero title and closing slides (`.slide--hero`,
  oversized display type).
- `slide-2` — a bare section-header slide, for dividing a deck into parts.
- `slide-3` — a two-column content + image layout, with a staggered bullet
  reveal (`.reveal-group` / `.reveal`, see `animation-patterns.md`).
- `slide-4` — a single hero stat with a count-up animation on entrance.
- `slide-5` — a pull-quote layout.
- A bottom progress bar (`#progress`) that fills as the deck advances.

`assets/` is where deck-specific images belong — replace the
`.image-frame` placeholder on `slide-3` with `<img src="assets/your.jpg">`
once real assets exist.
