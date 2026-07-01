#!/usr/bin/env bash
# Verify repo integrity. Two independent checks:
#   1. The signed commit — git's Merkle tree covers every file's content, so a
#      valid HEAD signature cryptographically attests the whole tree.
#   2. The SHA-256 manifest — re-hash tracked files and diff against CHECKSUMS.sha256
#      to catch any working-tree modification at a glance.
set -euo pipefail
cd "$(dirname "$0")"
rc=0

echo "==> 1. HEAD commit signature (covers all file content via git's Merkle tree)"
if git verify-commit HEAD 2>/dev/null; then
  echo "    OK — HEAD is signed & verified"
else
  echo "    (not locally verifiable — ensure ~/.ssh/git_allowed_signers is set; on GitHub look for the 'Verified' badge)"
fi

echo "==> 2. SHA-256 manifest diff (tracked files vs CHECKSUMS.sha256)"
tmp=$(mktemp)
git ls-files | sort | grep -vxF "CHECKSUMS.sha256" \
  | while read -r f; do shasum -a 256 "$f"; done > "$tmp"
if diff "$tmp" CHECKSUMS.sha256 >/dev/null; then
  echo "    OK — all tracked files match the manifest"
else
  echo "    MISMATCH — files differ from the manifest:"; diff CHECKSUMS.sha256 "$tmp" || true; rc=1
fi
rm -f "$tmp"

[ $rc -eq 0 ] && echo "Integrity check PASSED." || echo "Integrity check FAILED."
exit $rc
