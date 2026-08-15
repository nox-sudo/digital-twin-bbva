#!/usr/bin/env bash
# export-pdf.sh — render a frontend-slides deck's index.html to a paginated
# PDF (one physical page per slide) using headless Chromium.
#
# Usage:
#   scripts/export-pdf.sh <deck-dir>/index.html [output.pdf]
#
# Relies on the @media print rules in viewport-base.css: every .slide
# becomes one 1920x1080px page, laid out in document order, with no
# scale-to-fit and no letterboxing.

set -euo pipefail

usage() {
  echo "usage: $(basename "$0") <path-to-index.html> [output.pdf]" >&2
  exit 1
}

[ $# -ge 1 ] || usage

input="$1"
if [ ! -f "$input" ]; then
  echo "error: $input not found" >&2
  exit 1
fi

output="${2:-${input%.html}.pdf}"
input_abs="$(cd "$(dirname "$input")" && pwd)/$(basename "$input")"
output_abs="$(cd "$(dirname "$output")" 2>/dev/null && pwd)/$(basename "$output")" || {
  mkdir -p "$(dirname "$output")"
  output_abs="$(cd "$(dirname "$output")" && pwd)/$(basename "$output")"
}

find_chromium() {
  if [ -n "${CHROME_BIN:-}" ] && command -v "$CHROME_BIN" >/dev/null 2>&1; then
    echo "$CHROME_BIN"
    return
  fi
  for candidate in google-chrome-stable google-chrome chromium chromium-browser; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return
    fi
  done
  # Playwright's bundled Chromium (pre-installed in some environments).
  local pw_chrome
  pw_chrome="$(find /opt/pw-browsers -maxdepth 3 -type f -name chrome -path '*chrome-linux*' 2>/dev/null | head -n1)"
  if [ -n "$pw_chrome" ]; then
    echo "$pw_chrome"
    return
  fi
  if [ -x /opt/pw-browsers/chromium ]; then
    echo "/opt/pw-browsers/chromium"
    return
  fi
  return 1
}

chrome="$(find_chromium)" || {
  echo "error: no Chromium/Chrome binary found. Install one, or set CHROME_BIN." >&2
  exit 1
}

echo "using: $chrome"
echo "rendering: file://$input_abs -> $output_abs"

"$chrome" \
  --headless=new \
  --disable-gpu \
  --no-sandbox \
  --no-pdf-header-footer \
  --print-to-pdf="$output_abs" \
  --virtual-time-budget=8000 \
  --run-all-compositor-stages-before-draw \
  "file://$input_abs" \
  >/dev/null 2>&1 || {
    echo "error: Chromium exited with a failure — re-run without the redirects to see its output:" >&2
    echo "  $chrome --headless=new --print-to-pdf=\"$output_abs\" \"file://$input_abs\"" >&2
    exit 1
  }

if [ ! -s "$output_abs" ]; then
  echo "error: $output_abs was not created" >&2
  exit 1
fi

echo "wrote $output_abs"
