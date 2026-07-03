# Manuscript build process (MANDATORY)

Any manuscript in this repository is authored in **Markdown** (the source of truth,
version-controlled) and **must** be published to **both Word (`.docx`) and PDF (`.pdf`)**
before the writing task is considered complete. Producing only one format — or only the
Markdown — is an incomplete deliverable.

## The rule

> **Writing or editing a manuscript is not "done" until `scripts/build_manuscript.sh`
> has regenerated BOTH its `.docx` and `.pdf` from the current Markdown, and both
> artifacts exist and are non-empty.**

The wrapper enforces this: it exits non-zero if either output is missing, so it can be used
as a gate in scripts, hooks, or CI.

## How to run it

```bash
# one manuscript
scripts/build_manuscript.sh RESEARCH_PAPER_DETAILED.md

# all manuscripts at once
make manuscript
```

Each invocation produces, next to the source, `<name>.docx` and `<name>.pdf`.

## Pipeline

```
manuscript.md
   │  scripts/md_to_docx.js   (docx-js; headings, tables, lists, blockquotes,
   ▼                           $inline$ and $$display$$ math -> readable Unicode)
manuscript.docx
   │  LibreOffice (soffice --headless --convert-to pdf)
   ▼
manuscript.pdf
   │  build_manuscript.sh gate: BOTH must exist and be non-empty, else exit != 0
   ▼
done
```

## Prerequisites

- **Node.js** with the repo's `node_modules` (`docx` is pinned in `package.json`). Run
  `npm install` once.
- **LibreOffice** providing `soffice` (PDF step is mandatory, not optional). On macOS:
  `brew install --cask libreoffice`. The wrapper searches `PATH`, then the standard
  `/Applications/LibreOffice.app/...` and Homebrew locations.

No paid tool, no pandoc, and no LaTeX engine is required.

## What is tracked vs generated

- **Tracked (source):** the `*.md` manuscripts, `scripts/md_to_docx.js`,
  `scripts/build_manuscript.sh`, `scripts/build_paper.js` (the curated v1.0 Word generator),
  and `scripts/build_deck.js` (the slide deck).
- **Generated (git-ignored):** every `*.docx`, `*.pdf`, `*.pptx`, and the QA render images.
  They rebuild deterministically from source, so they are never committed.

## Math rendering note (for referees)

Because the toolchain intentionally avoids a LaTeX engine, `$...$` / `$$...$$` math is
transliterated to readable Unicode (Greek letters, `≤ → × √ Σ`, sub/superscripts, `x̄`).
Fractions render as `(a)/(b)`. This is faithful and legible but not typeset; a LaTeX/`pandoc`
path can be added later if journal submission requires typeset equations.

## Manuscripts currently under this process

| Markdown source | Description |
|---|---|
| `RESEARCH_PAPER.md` | v1.0 working paper (concise) |
| `RESEARCH_PAPER_DETAILED.md` | v2.0 extended, peer-review-oriented manuscript |
