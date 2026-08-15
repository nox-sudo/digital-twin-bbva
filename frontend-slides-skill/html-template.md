# HTML template

Copy this skeleton as `index.html` for a new deck. It wires up
`viewport-base.css`, the scale-to-fit script, and click/keyboard navigation.
Everything preset-specific goes in a sibling `deck.css` (see
`STYLE_PRESETS.md` and `bold-template-pack/` for a worked example) — don't
edit `viewport-base.css` itself.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Deck title</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="viewport-base.css" />
  <link rel="stylesheet" href="deck.css" />
</head>
<body>
  <div id="stage">
    <div id="deck">

      <section class="slide is-active" id="slide-1">
        <div class="eyebrow">Section label</div>
        <h1 class="slide-title">Deck title goes here</h1>
        <p class="lede">One-line subtitle or framing statement.</p>
        <div class="slide-footer">
          <span>Presenter / date</span>
          <span class="slide-counter"></span>
        </div>
      </section>

      <section class="slide" id="slide-2">
        <h2 class="slide-title">Second slide</h2>
        <!-- content -->
        <div class="slide-footer">
          <span>Presenter / date</span>
          <span class="slide-counter"></span>
        </div>
      </section>

      <!-- add more <section class="slide"> blocks here -->

    </div>
  </div>

  <script>
    (function () {
      const CANVAS_W = 1920;
      const CANVAS_H = 1080;
      const deck = document.getElementById("deck");
      const slides = Array.from(document.querySelectorAll(".slide"));
      let current = Math.max(0, slides.findIndex((s) => s.classList.contains("is-active")));

      function fitStage() {
        const scale = Math.min(
          window.innerWidth / CANVAS_W,
          window.innerHeight / CANVAS_H
        );
        deck.style.transform = `scale(${scale})`;
      }

      function showSlide(index) {
        if (index < 0 || index >= slides.length) return;
        slides[current].classList.remove("is-active");
        current = index;
        slides[current].classList.add("is-active");
        document.querySelectorAll(".slide-counter").forEach((el) => {
          el.textContent = `${current + 1} / ${slides.length}`;
        });
        history.replaceState(null, "", `#slide-${current + 1}`);
      }

      function next() { showSlide(current + 1); }
      function prev() { showSlide(current - 1); }

      window.addEventListener("resize", fitStage);
      window.addEventListener("keydown", (e) => {
        if (["ArrowRight", "PageDown", " "].includes(e.key)) next();
        if (["ArrowLeft", "PageUp"].includes(e.key)) prev();
      });
      document.getElementById("stage").addEventListener("click", (e) => {
        const half = window.innerWidth / 2;
        e.clientX > half ? next() : prev();
      });

      const hashIndex = parseInt((location.hash || "").replace("#slide-", ""), 10);
      if (!Number.isNaN(hashIndex) && hashIndex >= 1) current = hashIndex - 1;

      fitStage();
      showSlide(current);
    })();
  </script>
</body>
</html>
```

## Notes

- **One file per slide type, not per slide.** Reuse the same handful of
  slide layouts (title, section header, content + image, stat callout,
  quote, closing) rather than hand-crafting one-off markup for every slide.
- **`.slide-counter` and `.slide-footer` are optional** — drop them for a
  cleaner look, but keep them consistent across all slides if you use them.
- **Deep-linking works via `#slide-N`** — useful when sharing a link to a
  specific slide or when `export-pdf.sh` needs to render a single slide for
  debugging.
- **Images:** reference them as relative paths (`img/photo.jpg`) inside the
  deck folder so the whole thing stays portable — never hot-link to
  external URLs.
- **Keep `index.html` free of preset colors/fonts.** Anything visual belongs
  in `deck.css`, so swapping presets never means touching markup.
