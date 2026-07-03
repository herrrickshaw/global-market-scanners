# Global Market Scanners — build targets
#
# Manuscript publishing is a MANDATORY two-format step: every manuscript Markdown
# must yield BOTH a .docx and a .pdf (see docs/MANUSCRIPT_PROCESS.md).

MANUSCRIPTS := RESEARCH_PAPER.md RESEARCH_PAPER_DETAILED.md

.PHONY: manuscript deck test clean-artifacts

# Build Word + PDF for every manuscript (fails if either output is missing).
manuscript:
	scripts/build_manuscript.sh $(MANUSCRIPTS)

# Rebuild the slide deck (.pptx).
deck:
	NODE_PATH=./node_modules node scripts/build_deck.js

# Run the test suite.
test:
	pytest -q

# Remove generated documents and QA render images (keeps Markdown source).
clean-artifacts:
	rm -f *.docx *.pdf *.pptx slide-*.jpg page-*.jpg mp-*.jpg
