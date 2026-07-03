#!/usr/bin/env bash
#
# build_manuscript.sh — MANDATORY manuscript build step.
#
# For a given manuscript Markdown file, produce BOTH a Word (.docx) and a PDF,
# and fail (non-zero exit) if either is missing. Manuscript writing is not
# considered complete until this succeeds. See docs/MANUSCRIPT_PROCESS.md.
#
# Usage:   scripts/build_manuscript.sh <manuscript.md> [more.md ...]
# Output:  <manuscript>.docx and <manuscript>.pdf  (both gitignored artifacts)
#
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Locate a LibreOffice binary (PDF generation is mandatory, not optional).
find_soffice() {
  if command -v soffice >/dev/null 2>&1; then command -v soffice; return; fi
  if command -v libreoffice >/dev/null 2>&1; then command -v libreoffice; return; fi
  for p in \
    "/Applications/LibreOffice.app/Contents/MacOS/soffice" \
    "/opt/homebrew/bin/soffice" \
    "/usr/bin/soffice" "/usr/local/bin/soffice"; do
    [ -x "$p" ] && { echo "$p"; return; }
  done
  return 1
}

SOFFICE="$(find_soffice || true)"
if [ -z "$SOFFICE" ]; then
  echo "ERROR: LibreOffice (soffice) not found. PDF generation is a mandatory step." >&2
  echo "       Install LibreOffice (e.g. 'brew install --cask libreoffice') and retry." >&2
  exit 2
fi

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <manuscript.md> [more.md ...]" >&2
  exit 2
fi

rc=0
for MD in "$@"; do
  if [ ! -f "$MD" ]; then echo "ERROR: manuscript not found: $MD" >&2; rc=1; continue; fi
  base="${MD%.md}"
  DOCX="${base}.docx"
  PDF="${base}.pdf"
  name="$(basename "$MD")"

  echo "==> [$name] Markdown -> Word (.docx)"
  NODE_PATH="$REPO/node_modules" node "$REPO/scripts/md_to_docx.js" "$MD" "$DOCX"

  echo "==> [$name] Word -> PDF (LibreOffice)"
  "$SOFFICE" --headless --convert-to pdf --outdir "$(dirname "$DOCX")" "$DOCX" >/dev/null 2>&1 || true

  # Mandatory gate: both artifacts must exist and be non-empty.
  if [ -s "$DOCX" ] && [ -s "$PDF" ]; then
    echo "    OK  $DOCX"
    echo "    OK  $PDF"
  else
    echo "ERROR: [$name] build incomplete — need BOTH .docx and .pdf." >&2
    [ -s "$DOCX" ] || echo "       missing/empty: $DOCX" >&2
    [ -s "$PDF" ]  || echo "       missing/empty: $PDF"  >&2
    rc=1
  fi
done

exit $rc
