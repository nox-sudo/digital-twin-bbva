#!/usr/bin/env bash
# deploy.sh — ship a frontend-slides deck folder.
#
# Usage:
#   scripts/deploy.sh <deck-dir> --serve [port]     Preview locally (default: port 8000)
#   scripts/deploy.sh <deck-dir> --out <target-dir>  Copy the deck's static files into target-dir
#   scripts/deploy.sh <deck-dir> --zip <out.zip>     Bundle the deck into a zip for manual upload
#   scripts/deploy.sh <deck-dir> --gh-pages [branch] Publish the deck to a GitHub Pages branch
#                                                     (default branch: gh-pages) of the current repo
#
# Exactly one action flag at a time. Run with no action flag to default to --serve.

set -euo pipefail

usage() {
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

[ $# -ge 1 ] || usage
deck_dir="$1"
shift

[ -d "$deck_dir" ] || { echo "error: $deck_dir is not a directory" >&2; exit 1; }
[ -f "$deck_dir/index.html" ] || echo "warning: $deck_dir/index.html not found — is this a deck folder?" >&2

action="${1:-serve}"
case "$action" in
  --serve|serve)
    port="${2:-8000}"
    echo "serving $deck_dir at http://localhost:$port (Ctrl+C to stop)"
    if command -v python3 >/dev/null 2>&1; then
      (cd "$deck_dir" && python3 -m http.server "$port")
    else
      echo "error: python3 not found for the preview server" >&2
      exit 1
    fi
    ;;

  --out)
    target="${2:?usage: deploy.sh <deck-dir> --out <target-dir>}"
    mkdir -p "$target"
    cp -R "$deck_dir"/. "$target"/
    echo "copied $deck_dir/ -> $target/"
    ;;

  --zip)
    out_zip="${2:?usage: deploy.sh <deck-dir> --zip <out.zip>}"
    command -v zip >/dev/null 2>&1 || { echo "error: zip is not installed" >&2; exit 1; }
    out_zip_abs="$(cd "$(dirname "$out_zip")" && pwd)/$(basename "$out_zip")"
    (cd "$deck_dir" && zip -r -q "$out_zip_abs" .)
    echo "wrote $out_zip_abs"
    ;;

  --gh-pages)
    branch="${2:-gh-pages}"
    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
      echo "error: not inside a git repository" >&2
      exit 1
    }
    if [ -n "$(git status --porcelain)" ]; then
      echo "error: working tree has uncommitted changes — commit or stash before publishing" >&2
      exit 1
    fi
    echo "publishing $deck_dir/ to branch '$branch' via git subtree split + push"
    remote="${GIT_REMOTE:-origin}"
    tmp_branch="deploy-tmp-$(date +%s)"
    git subtree split --prefix "$deck_dir" -b "$tmp_branch"
    git push "$remote" "$tmp_branch:$branch" --force
    git branch -D "$tmp_branch"
    echo "pushed $deck_dir/ to $remote/$branch"
    echo "note: --force rewrites '$branch' history on $remote; confirm that's intended before running this against a shared branch."
    ;;

  *)
    usage
    ;;
esac
