# Security & Integrity

How the repo protects its data and makes files tamper-evident. Honest scope: a
public git repo can't be made *immutable* (the owner can always change files), but
tampering is made **detectable** (signatures + checksums) and **resistant**
(branch protection blocks history rewrites).

## 1. No secrets in the repo
- **All credentials are read from environment variables** — `DNB_KEY`, `DNB_SECRET`,
  `LUSHA_API_KEY`, `APOLLO_API_KEY`, `OPENCORPORATES_API_TOKEN`,
  `SHARADAR_API_KEY`, `ANTHROPIC_API_KEY`. None are hardcoded or committed.
- Working tree **and full git history scanned** — no API keys, tokens, or private keys.
- Only public market data is stored (OHLC, filings, firmographics) — nothing sensitive.
- Heavy/derived caches (`edgar_facts.db`, `fundamentals_cache.db`, `market.duckdb`,
  raw `viability.db`, scan xlsx) are gitignored — not published.

## 2. Signed commits (tamper-evident authorship)
Commits are **SSH-signed**. Any alteration to a commit's content invalidates its
signature. Verify locally:
```bash
git log --show-signature -1
git verify-commit HEAD
```
> **One step for the maintainer:** register the signing public key on GitHub so
> commits show a green **Verified** badge, then require signatures (see §5):
> ```bash
> gh auth refresh -h github.com -s admin:ssh_signing_key
> gh ssh-key add ~/.ssh/git_signing.pub --type signing --title git-signing
> ```
> Signing public key:
> `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAID7AxnULfNGl7irrTiLRFfTQszchDlI2XsPyaYkHP3pq`

## 3. Branch protection (history is tamper-resistant)
`main` is protected — enforced even for admins:
- ❌ force-pushes blocked → **history cannot be rewritten**
- ❌ branch deletion blocked
- ✅ `enforce_admins: true`

So no one can silently rewrite or delete committed history; changes only move forward
as new (signed) commits.

## 4. Integrity manifest (verify the whole tree)
`CHECKSUMS.sha256` lists the SHA-256 of every tracked file, and
`CHECKSUMS.sha256.sig` is its SSH signature. Anyone can verify nothing was altered:
```bash
./verify_integrity.sh          # re-hash tracked files, diff against the manifest, check the signature
```
Regenerate after intended changes:
```bash
git ls-files | sort | while read f; do shasum -a 256 "$f"; done > CHECKSUMS.sha256
ssh-keygen -Y sign -f ~/.ssh/git_signing -n file CHECKSUMS.sha256
```

## 5. To fully lock it down (maintainer, after registering the signing key)
```bash
# require every commit on main to be signed & verified by GitHub
gh api -X POST repos/herrrickshaw/global-market-scanners/branches/main/protection/required_signatures
```
(Do this **only after** the signing key is registered on GitHub, or pushes will be rejected.)

## What "tamper-proof" means here
| Guarantee | Mechanism |
|---|---|
| No leaked secrets | env-var credentials + history scan |
| Content alteration is detectable | signed commits + signed SHA-256 manifest |
| History can't be rewritten/deleted | branch protection (no force-push/delete, enforce-admins) |
| GitHub-verified authorship | signing key registered → "Verified" badge (maintainer step) |

Report a security issue by opening a private advisory on the repo's Security tab.
