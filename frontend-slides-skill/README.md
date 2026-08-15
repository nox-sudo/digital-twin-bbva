# frontend-slides (Claude Code skill source)

Source files for the `frontend-slides` Claude Code skill — building
presentation decks as viewport-scaled HTML/CSS pages. See `SKILL.md` for
the full workflow.

This directory is not part of the digital-twin-bbva application; it's kept
here as the versioned source for the skill. To install it locally:

```bash
mkdir -p ~/.claude/skills/frontend-slides/scripts
cp SKILL.md STYLE_PRESETS.md viewport-base.css html-template.md animation-patterns.md ~/.claude/skills/frontend-slides/
cp -R bold-template-pack ~/.claude/skills/frontend-slides/
cp scripts/extract-pptx.py scripts/deploy.sh scripts/export-pdf.sh ~/.claude/skills/frontend-slides/scripts/
```

Run those commands from inside this directory (`frontend-slides-skill/`).
