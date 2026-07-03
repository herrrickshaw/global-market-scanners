#!/usr/bin/env node
/*
 * md_to_docx.js — generic Markdown -> Word (.docx) converter for manuscripts.
 *
 * Part of the MANDATORY manuscript build pipeline (see docs/MANUSCRIPT_PROCESS.md).
 * Handles the Markdown subset the project's manuscripts use:
 *   - ATX headings (#, ##, ###, ####); first H1 becomes the document title
 *   - paragraphs with inline **bold**, *italic*, `code`, and $math$ (delimiters stripped)
 *   - unordered ("- ") and ordered ("1. ") lists
 *   - GitHub pipe tables with a |---| separator row
 *   - blockquotes ("> ")
 *   - display equations fenced by $$ ... $$
 *   - horizontal rules (---)  ->  thin bottom-border rule
 * Anything unrecognised is emitted as a plain paragraph, so output is never lossy.
 *
 * Usage:  node md_to_docx.js <input.md> <output.docx>
 * Deps:   docx (npm)   — run with NODE_PATH pointing at the repo node_modules.
 */
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, HeadingLevel, BorderStyle,
  WidthType, ShadingType, PageNumber,
} = require("docx");

const [, , inPath, outPath] = process.argv;
if (!inPath || !outPath) { console.error("usage: md_to_docx.js <in.md> <out.docx>"); process.exit(2); }

// ---- LaTeX -> readable Unicode (for $inline$ and $$display$$ math) --------
const SYM = { // applied longest-key-first so \leq wins over \le, etc.
  "\\varepsilon": "ε", "\\epsilon": "ε", "\\alpha": "α", "\\beta": "β", "\\gamma": "γ",
  "\\delta": "δ", "\\lambda": "λ", "\\sigma": "σ", "\\rho": "ρ", "\\tau": "τ", "\\phi": "φ",
  "\\theta": "θ", "\\mu": "μ", "\\pi": "π", "\\Sigma": "Σ", "\\Delta": "Δ", "\\Omega": "Ω",
  "\\rightarrow": "→", "\\leftarrow": "←", "\\to": "→", "\\leq": "≤", "\\le": "≤",
  "\\geq": "≥", "\\ge": "≥", "\\times": "×", "\\cdot": "·", "\\approx": "≈", "\\neq": "≠",
  "\\pm": "±", "\\in": "∈", "\\langle": "⟨", "\\rangle": "⟩", "\\sum": "Σ", "\\prod": "Π",
  "\\infty": "∞", "\\ldots": "…", "\\dots": "…", "\\cdots": "⋯", "\\gtrsim": "≳", "\\lesssim": "≲",
};
const SUP = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷",
  "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "(": "⁽", ")": "⁾", n: "ⁿ", i: "ⁱ" };
const SUB = { "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇",
  "8": "₈", "9": "₉", "+": "₊", "-": "₋", "(": "₍", ")": "₎", a: "ₐ", e: "ₑ", h: "ₕ", i: "ᵢ",
  j: "ⱼ", k: "ₖ", l: "ₗ", m: "ₘ", n: "ₙ", o: "ₒ", p: "ₚ", r: "ᵣ", s: "ₛ", t: "ₜ", u: "ᵤ",
  v: "ᵥ", x: "ₓ" };
const conv = (g, map, pre) => ([...g].every((c) => map[c] || c === " ")
  ? [...g].map((c) => map[c] || " ").join("") : pre + "(" + g + ")");
function prettifyMath(s) {
  s = s.replace(/\\(left|right|big|Big|bigg|Bigg)\b/g, "");
  s = s.replace(/\\q?quad\b/g, " ");
  s = s.replace(/\\(text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}/g, "$2");
  s = s.replace(/\\bar\{([^{}]*)\}/g, "$1̄").replace(/\\hat\{([^{}]*)\}/g, "$1̂")
       .replace(/\\tilde\{([^{}]*)\}/g, "$1̃").replace(/\\vec\{([^{}]*)\}/g, "$1⃗");
  for (const k of Object.keys(SYM).sort((a, b) => b.length - a.length)) s = s.split(k).join(SYM[k]);
  for (let p = 0; p < 3; p++) s = s.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, "($1)/($2)");
  s = s.replace(/\\sqrt\{([^{}]*)\}/g, "√($1)");
  s = s.replace(/\\([%$&#_{}])/g, "$1");   // escaped literals: \% -> %, \_ -> _, etc.
  s = s.replace(/\\[,;:! ]/g, " ");
  s = s.replace(/\^\{([^{}]*)\}/g, (m, g) => conv(g, SUP, "^")).replace(/\^(\w)/g, (m, g) => conv(g, SUP, "^"));
  s = s.replace(/_\{([^{}]*)\}/g, (m, g) => conv(g, SUB, "_")).replace(/_(\w)/g, (m, g) => conv(g, SUB, "_"));
  s = s.replace(/\\[a-zA-Z]+/g, "").replace(/[{}]/g, "");
  return s.replace(/\s+/g, " ").trim();
}

const NAVY = "1F3864", NAVY2 = "2E5090", GREY = "666666", RULE = "1F3864";
const CONTENT_W = 9360;                 // US Letter, 1" margins (12240 - 2*1440)
const src = fs.readFileSync(inPath, "utf8").replace(/\r\n/g, "\n");
const lines = src.split("\n");

// ---- inline parser: string -> TextRun[] (stateful scanner, handles nesting) ----
// Toggles bold (**), italic (*), code (`); pulls $math$ out and prettifies it.
// A stateful scan (not a single-pass regex) correctly handles **bold with *italic*
// inside** and gracefully tolerates markers that span folded line-wraps.
function inline(text, base = {}) {
  const runs = [];
  let bold = false, italic = false, code = false, cur = "";
  const flush = () => {
    if (!cur) return;
    runs.push(new TextRun({
      text: cur,
      bold: !!(base.bold || bold),
      italics: !!(base.italics || italic),
      font: code ? "Consolas" : base.font,
      color: base.color,
    }));
    cur = "";
  };
  for (let k = 0; k < text.length;) {
    if (!code && text.startsWith("**", k)) { flush(); bold = !bold; k += 2; continue; }
    const ch = text[k];
    if (!code && ch === "*") { flush(); italic = !italic; k++; continue; }
    if (ch === "`") { flush(); code = !code; k++; continue; }
    if (!code && ch === "$") {
      const end = text.indexOf("$", k + 1);
      if (end > k) {
        flush();
        runs.push(new TextRun({ text: prettifyMath(text.slice(k + 1, end)),
          italics: true, bold: !!base.bold, color: base.color }));
        k = end + 1; continue;
      }
    }
    cur += ch; k++;
  }
  flush();
  return runs.length ? runs : [new TextRun({ text: "", ...base })];
}

const cleanCell = (s) => s.trim();
const isTableSep = (s) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(s);
const isTableRow = (s) => /^\s*\|.*\|\s*$/.test(s.trim()) || (s.includes("|") && s.trim().startsWith("|"));
const splitRow = (s) => s.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cleanCell);

const children = [];
let title = null;
let olInstance = 0;   // distinct numbering instance per ordered-list block (restarts each list)
let ulInstance = 0;   // distinct instance per bullet-list block (so Word renders every first bullet)

// ---- block state machine ------------------------------------------------
let i = 0;
function hr() {
  return new Paragraph({ spacing: { before: 120, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 1 } },
    children: [new TextRun("")] });
}
function heading(level, text) {
  const map = { 1: HeadingLevel.HEADING_1, 2: HeadingLevel.HEADING_2, 3: HeadingLevel.HEADING_3, 4: HeadingLevel.HEADING_4 };
  return new Paragraph({ heading: map[level] || HeadingLevel.HEADING_4, children: inline(text) });
}
function table(rows) {
  const header = splitRow(rows[0]);
  const body = rows.slice(2).map(splitRow);            // rows[1] is the separator
  const n = header.length;
  const colW = Math.floor(CONTENT_W / n);
  const widths = Array(n).fill(colW);
  widths[n - 1] += CONTENT_W - colW * n;               // absorb rounding
  const border = { style: BorderStyle.SINGLE, size: 1, color: "BBBBBB" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const mkRow = (cells, head) => new TableRow({
    tableHeader: !!head,
    children: cells.map((c, j) => new TableCell({
      borders, width: { size: widths[j], type: WidthType.DXA },
      shading: head ? { fill: NAVY, type: ShadingType.CLEAR, color: "auto" } : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({ children: inline(c, head ? { bold: true, color: "FFFFFF" } : {}),
        spacing: { after: 0 } })],
    })),
  });
  const trs = [mkRow(header, true), ...body.map((r) => {
    while (r.length < n) r.push("");                    // pad ragged rows
    return mkRow(r, false);
  })];
  return new Table({ width: { size: CONTENT_W, type: WidthType.DXA }, columnWidths: widths, rows: trs });
}
const indentOf = (s) => (s.match(/^ */) || [""])[0].length;
// Parse a $$...$$ display-equation starting at index idx; return {para, next}.
function parseDisplayMathAt(idx) {
  const first = lines[idx].replace(/^\s*\$\$/, "");
  let buf, next;
  if (/\$\$\s*$/.test(first)) { buf = [first.replace(/\$\$\s*$/, "")]; next = idx + 1; }
  else {
    buf = [first]; let k = idx + 1;
    while (k < lines.length && !/\$\$\s*$/.test(lines[k])) { buf.push(lines[k]); k++; }
    if (k < lines.length) buf.push(lines[k].replace(/\$\$\s*$/, ""));
    next = k + 1;
  }
  const para = new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 100, after: 100 },
    children: [new TextRun({ text: prettifyMath(buf.join(" ")), italics: true, font: "Cambria Math" })] });
  return { para, next };
}

while (i < lines.length) {
  let line = lines[i];

  // blank
  if (/^\s*$/.test(line)) { i++; continue; }

  // horizontal rule
  if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { children.push(hr()); i++; continue; }

  // heading
  let hm = line.match(/^(#{1,6})\s+(.*)$/);
  if (hm) {
    const lvl = hm[1].length, txt = hm[2].trim();
    if (lvl === 1 && !title) { title = txt; children.push(heading(1, txt)); }
    else children.push(heading(lvl, txt));
    i++; continue;
  }

  // display math  $$ ... $$
  if (/^\s*\$\$/.test(line)) {
    const r = parseDisplayMathAt(i); children.push(r.para); i = r.next; continue;
  }

  // table
  if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
    const rows = [line, lines[i + 1]]; i += 2;
    while (i < lines.length && isTableRow(lines[i]) && !isTableSep(lines[i])) { rows.push(lines[i]); i++; }
    children.push(table(rows));
    children.push(new Paragraph({ spacing: { after: 80 }, children: [new TextRun("")] }));
    continue;
  }

  // blockquote
  if (/^\s*>\s?/.test(line)) {
    const buf = [];
    while (i < lines.length && /^\s*>\s?/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
    children.push(new Paragraph({ indent: { left: 480 }, spacing: { before: 80, after: 80 },
      border: { left: { style: BorderStyle.SINGLE, size: 12, color: "BBBBBB", space: 12 } },
      children: inline(buf.join(" "), { italics: true, color: GREY }) }));
    continue;
  }

  // unordered list — own instance per block (Word renders every first bullet);
  // folds wrapped continuation lines into each bullet
  if (/^\s*[-*]\s+/.test(line)) {
    ulInstance += 1;
    while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
      const lvl = Math.min(2, Math.floor(indentOf(lines[i]) / 2));
      const wbuf = [lines[i].replace(/^\s*[-*]\s+/, "")]; i++;
      while (i < lines.length && !/^\s*$/.test(lines[i]) && indentOf(lines[i]) >= 2 &&
             !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i]) &&
             !/^\s*\$\$/.test(lines[i])) {
        wbuf.push(lines[i].trimStart()); i++;
      }
      children.push(new Paragraph({ numbering: { reference: "ul", level: lvl, instance: ulInstance },
        children: inline(wbuf.join(" ")) }));
    }
    continue;
  }

  // ordered list — one instance per block (restarts at 1); folds continuation
  // lines, nested bullets, and per-item display equations into the same list.
  if (/^\d+\.\s+/.test(line) && indentOf(line) === 0) {
    olInstance += 1; ulInstance += 1;   // ulInstance for any nested bullets in this block
    while (i < lines.length) {
      const L = lines[i];
      if (/^\s*$/.test(L)) {                    // blank: continue list only if more item content follows
        let j = i + 1; while (j < lines.length && /^\s*$/.test(lines[j])) j++;
        if (j < lines.length && (indentOf(lines[j]) >= 2 || /^\d+\.\s+/.test(lines[j]))) { i = j; continue; }
        i = j; break;
      }
      if (/^\d+\.\s+/.test(L) && indentOf(L) === 0) {     // a numbered item
        children.push(new Paragraph({ numbering: { reference: "ol", level: 0, instance: olInstance },
          children: inline(L.replace(/^\d+\.\s+/, "")) }));
        i++; continue;
      }
      if (indentOf(L) >= 2) {                    // content belonging to the current item
        const c = L.trimStart();
        if (/^\$\$/.test(c)) { const r = parseDisplayMathAt(i); children.push(r.para); i = r.next; continue; }
        if (/^[-*]\s+/.test(c)) {
          children.push(new Paragraph({ numbering: { reference: "ul", level: 1, instance: ulInstance },
            children: inline(c.replace(/^[-*]\s+/, "")) }));
          i++; continue;
        }
        const wbuf = [c]; i++;                    // fold wrapped continuation text
        while (i < lines.length && !/^\s*$/.test(lines[i]) && indentOf(lines[i]) >= 2) {
          const cc = lines[i].trimStart();
          if (/^\$\$/.test(cc) || /^[-*]\s+/.test(cc) || /^\d+\.\s+/.test(cc)) break;
          wbuf.push(cc); i++;
        }
        children.push(new Paragraph({ indent: { left: 620 }, spacing: { after: 100 }, children: inline(wbuf.join(" ")) }));
        continue;
      }
      break;                                      // dedented non-list line ends the list
    }
    continue;
  }

  // paragraph: accumulate consecutive plain lines
  const buf = [line]; i++;
  while (i < lines.length && !/^\s*$/.test(lines[i]) &&
         !/^(#{1,6})\s/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) &&
         !/^\s*\d+\.\s+/.test(lines[i]) && !/^\s*>\s?/.test(lines[i]) &&
         !/^\s*\$\$/.test(lines[i]) && !/^\s*(-{3,}|\*{3,})\s*$/.test(lines[i]) &&
         !(isTableRow(lines[i]) && i + 1 < lines.length && isTableSep(lines[i + 1]))) {
    buf.push(lines[i]); i++;
  }
  children.push(new Paragraph({ spacing: { after: 120 }, children: inline(buf.join(" ")) }));
}

// ---- assemble document --------------------------------------------------
const doc = new Document({
  creator: "Umashankar Triplicane Dwarakanathan",
  title: title || "Manuscript",
  styles: {
    default: { document: { run: { font: "Arial", size: 21 } } }, // 10.5pt body
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 300, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 23, bold: true, font: "Arial", color: NAVY2 },
        paragraph: { spacing: { before: 180, after: 100 }, outlineLevel: 2 } },
      { id: "Heading4", name: "Heading 4", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 21, bold: true, italics: true, font: "Arial", color: NAVY2 },
        paragraph: { spacing: { before: 140, after: 80 }, outlineLevel: 3 } },
    ],
  },
  numbering: {
    config: [
      { reference: "ul", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 480, hanging: 240 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 960, hanging: 240 } } } },
        { level: 2, format: LevelFormat.BULLET, text: "▪", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1440, hanging: 240 } } } },
      ] },
      { reference: "ol", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 480, hanging: 240 } } } },
      ] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ text: (title || "Manuscript") + "   ·   Page ", size: 16, color: GREY }),
        new TextRun({ children: [PageNumber.CURRENT], size: 16, color: GREY })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(outPath, buf); console.log("wrote", outPath, buf.length, "bytes"); });
