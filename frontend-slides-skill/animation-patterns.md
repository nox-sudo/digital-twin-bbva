# Animation patterns

Copy-paste patterns for motion. All of them are already muted by the
`prefers-reduced-motion` block in `viewport-base.css`, so you don't need to
special-case that yourself — just use these classes/attributes normally.

Rules of thumb:
- Motion should reveal hierarchy (what to look at first), not decorate.
- Keep entrance animations under ~500ms; anything longer reads as slow.
- Never animate more than one "group" at once on a slide — stagger instead
  of running everything simultaneously.
- Between-slide transitions should be one consistent style for the whole
  deck. Don't mix fade and slide transitions within the same deck.

## Entrance reveal (single element)

Add `.reveal` to any element. It fades and rises slightly when its slide
becomes active.

```css
.reveal {
  opacity: 0;
  transform: translateY(24px);
  transition: opacity 420ms ease, transform 420ms ease;
}

.slide.is-active .reveal {
  opacity: 1;
  transform: translateY(0);
}
```

## Staggered list reveal

Use `.reveal-group` on the container and `.reveal` on each child; set
`--delay-step` to control the gap between items.

```css
.reveal-group {
  --delay-step: 90ms;
}

.reveal-group .reveal {
  transition-delay: calc(var(--i, 0) * var(--delay-step));
}
```

```html
<ul class="reveal-group">
  <li class="reveal" style="--i:0">First point</li>
  <li class="reveal" style="--i:1">Second point</li>
  <li class="reveal" style="--i:2">Third point</li>
</ul>
```

If the list is generated rather than hand-written, set `--i` from JS instead
of inline in the template:

```js
document.querySelectorAll(".reveal-group .reveal").forEach((el, i) => {
  el.style.setProperty("--i", i);
});
```

## Stat count-up

For `.stat-number` elements, animate the number counting up when the slide
becomes active rather than just fading it in — it reads as more deliberate
for a single hero metric.

```js
function animateCountUp(el, { to, duration = 900, prefix = "", suffix = "" }) {
  const start = performance.now();
  function tick(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3); // ease-out-cubic
    el.textContent = prefix + Math.round(to * eased).toLocaleString() + suffix;
    if (t < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

// call when the slide containing it becomes active, e.g. from showSlide()
// in html-template.md:
// animateCountUp(document.querySelector("#slide-4 .stat-number"), { to: 42, suffix: "%" });
```

Set the element's initial text content to the **final** value (`42%`, not
`0%`), and let the animation overwrite it on activation. PDF export
(`scripts/export-pdf.sh`) forces every slide to its resting state without
running the activation JS, so a placeholder like `0%` would print wrong.

## Between-slide transitions

`html-template.md`'s `showSlide()` swaps `.is-active` instantly by default.
To animate the swap, cross-fade the outgoing/incoming slide instead:

```css
.slide {
  transition: opacity 320ms ease;
}
```

That's sufficient given `.slide`'s existing `opacity` toggle in
`viewport-base.css` — no extra classes needed. For a directional slide
transition instead of a fade, add:

```css
.slide {
  transition: opacity 320ms ease, transform 320ms ease;
  transform: translateX(0);
}

.slide:not(.is-active) {
  transform: translateX(24px);
}
```

Pick fade *or* directional slide for the whole deck, not both.

## Progress bar

A thin bar across the top/bottom of `#stage` that fills as the deck
progresses — nice for long decks so the audience can gauge remaining length.

```css
#progress {
  position: fixed;
  left: 0;
  bottom: 0;
  height: 4px;
  background: var(--accent);
  transition: width 250ms ease;
  z-index: 10;
}
```

```js
function updateProgress() {
  const pct = ((current + 1) / slides.length) * 100;
  document.getElementById("progress").style.width = pct + "%";
}
// call updateProgress() at the end of showSlide() in html-template.md
```
