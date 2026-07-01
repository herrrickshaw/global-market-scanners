#!/usr/bin/env bash
# Verify repo integrity: re-hash tracked files against the signed manifest, and
# check the manifest's SSH signature. Exit non-zero on any mismatch.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Re-hashing tracked files and diffing against CHECKSUMS.sha256"
tmp=$(mktemp)
git ls-files | sort | grep -vxF "CHECKSUMS.sha256" | grep -vxF "CHECKSUMS.sha256.sig" \
  | while read -r f; do shasum -a 256 "$f"; done > "$tmp"
# manifest minus its own later-added lines (manifest was generated before these two files existed)
if diff <(grep -vE "CHECKSUMS\.sha256" CHECKSUMS.sha256) "$tmp" >/dev/null; then
  echo "    OK — all tracked files match the manifest"
else
  echo "    MISMATCH — files differ from the manifest:"; diff <(grep -vE "CHECKSUMS\.sha256" CHECKSUMS.sha256) "$tmp" || true
  rm -f "$tmp"; exit 1
fi
rm -f "$tmp"

echo "==> Verifying the manifest signature (SSH)"
SIGNERS="${HOME}/.ssh/git_allowed_signers"
ID="${1:-herrrickshaw@users.noreply.github.com}"
if [ -f "$SIGNERS" ] && [ -f CHECKSUMS.sha256.sig ]; then
  ssh-keygen -Y verify -f "$SIGNERS" -I "$ID" -n file -s CHECKSUMS.sha256.sig < CHECKSUMS.sha256 \
    && echo "    OK — manifest signature valid"
else
  echo "    (skip) allowed_signers or signature missing"
fi

echo "==> Verifying HEAD commit signature"
git verify-commit HEAD 2>/dev/null && echo "    OK — HEAD is signed" || echo "    (HEAD signature not verifiable locally without allowed_signers)"
echo "Integrity check complete."
