# Style presets

A preset is just a `deck.css` that sets the CSS custom properties and a few
component overrides defined by `viewport-base.css`. Pick one, copy its
starting point, and adjust the palette/type to the user's brand rather than
inventing layout from scratch.

Every preset must set at minimum: `--bg`, `--fg`, `--accent`, `--font-body`,
`--font-display`.

## Bold

**When to use:** pitch decks, launches, anything that needs to read from the
back of a room. High contrast, oversized type, one accent color used hard.

- Palette: near-black or pure-white background, a single saturated accent
  (not a gradient), text at max contrast against the background.
- Type: a heavy display weight (700–900) for titles, a plain grotesque for
  body copy. Titles are allowed to break the 88px default upward for title
  slides.
- Spacing: generous — lean on `--safe-margin` and `.gap-lg`, avoid cramming
  more than one idea per slide.
- Reference implementation: `bold-template-pack/`. Copy `bold.css` as your
  starting `deck.css` and adjust `--accent`.

```css
:root {
  --bg: #0b0b0c;
  --fg: #f5f5f5;
  --accent: #ff5a36;
  --font-body: "Inter", sans-serif;
  --font-display: "Inter", sans-serif;
}
```

## Minimal

**When to use:** internal reviews, technical readouts, anything where the
content should do the talking and the chrome should disappear.

- Palette: white or off-white background, near-black text, a muted accent
  used only for the eyebrow label and small highlights — never for large
  fills.
- Type: a plain, slightly smaller scale than Bold (drop `h1.slide-title` to
  ~64px, body to ~26px) so more content fits per slide without feeling
  cramped.
- Spacing: tighter than Bold; it's fine to have a two-column layout with a
  chart on one side and 3–4 bullet points on the other.

```css
:root {
  --bg: #ffffff;
  --fg: #1a1a1a;
  --accent: #6b7280;
  --font-body: "Inter", sans-serif;
  --font-display: "Inter", sans-serif;
}
```

## Editorial

**When to use:** narrative/story-driven decks — company updates, "state of
the business" readouts, anything meant to be read more than presented.

- Palette: warm off-white background, ink-black text, a restrained accent
  (a muted red or forest green) used sparingly for pull-quotes and rules.
- Type: pair a serif display font for titles/pull-quotes with a sans body
  font. Set generous line-height (1.5+) on body copy.
- Spacing: use a visible rule (`border-top`) or thin divider between the
  eyebrow and title on section slides to reinforce the "printed page" feel.

```css
:root {
  --bg: #faf7f2;
  --fg: #1c1a17;
  --accent: #8a1f1f;
  --font-body: "Source Serif 4", Georgia, serif;
  --font-display: "Source Serif 4", Georgia, serif;
}
```

## Dark tech

**When to use:** product/engineering decks, developer-facing launches,
anything that benefits from a code-editor aesthetic.

- Palette: dark slate background, off-white text, a single bright accent
  (electric blue, cyan, or lime) reserved for links, code highlights, and
  small UI chrome — never large background fills.
- Type: a geometric sans for titles, a monospace font for any code, stats,
  or metrics to reinforce the "technical" feel.
- Spacing: fine to use a subtle grid/dot background texture behind content,
  as long as it stays low-contrast enough not to compete with text.

```css
:root {
  --bg: #0d1117;
  --fg: #e6edf3;
  --accent: #58a6ff;
  --font-body: "Inter", sans-serif;
  --font-display: "Inter", sans-serif;
}
```

## Choosing between them

Ask (or infer from the brief):
1. **Audience** — external/investor-facing leans Bold; internal/technical
   leans Minimal or Dark tech; narrative updates lean Editorial.
2. **Density** — few big ideas per slide → Bold; lots of detail per slide →
   Minimal or Editorial.
3. **Brand assets given** — if the user supplies brand colors/fonts, start
   from whichever preset's spacing/type philosophy is the closest match and
   swap in their tokens, rather than picking by color alone.

When in doubt, default to Bold for anything presented live, Minimal for
anything primarily read on a screen.
