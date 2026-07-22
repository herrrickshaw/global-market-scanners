# DATA_ACCESS — how to get this repo's data

> ⚠️ **Git LFS in this repo is currently unreachable** — the account's LFS budget
> is exhausted, so `git clone` / `git lfs pull` cannot download the data files
> (they arrive as ~130-byte pointer stubs). This is account-wide, not specific to
> this repo. Clone with `GIT_LFS_SKIP_SMUDGE=1 git clone …` to avoid errors.

## This repo's LFS footprint (audit 2026-07-22)

| LFS objects | Total size |
|---|---|
| 6 | 2.1 MB |

## Where the data actually comes from

2 MB — scan outputs, regenerable by re-running the scanners.

## Account-wide context

- Full pointer inventory, dedup plan and audit tooling:
  [`herrrickshaw/repo-data-dedup`](https://github.com/herrrickshaw/repo-data-dedup)
- Source catalogue + re-collection SOP for every dataset:
  [`SOP_DATA_SOURCES.md`](https://github.com/herrrickshaw/repo-data-dedup/blob/main/SOP_DATA_SOURCES.md)
- Migration recipe off LFS:
  [`PLAYBOOK.md`](https://github.com/herrrickshaw/repo-data-dedup/blob/main/PLAYBOOK.md)
- **Policy: do not add new LFS objects** — they would be born unreachable. New data
  goes in as gzipped/parquet regular git objects under 50 MB, one canonical format,
  with its collector script committed alongside.
