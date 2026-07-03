const fs = require("fs");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType, ShadingType,
  TableOfContents, Footer, Header, PageNumber, PageBreak } = require("docx");

const CW = 9360; // content width US Letter, 1" margins
const H = (t, lvl) => new Paragraph({ heading: lvl, children: [new TextRun(t)] });
const P = (t, opts={}) => new Paragraph({ spacing:{after:120}, ...opts, children: Array.isArray(t)?t:[new TextRun(t)] });
const B = (runs) => new TextRun({ bold:true, children: runs? undefined:undefined, text: runs });
const li = (t, ref="bul") => new Paragraph({ numbering:{reference:ref,level:0}, spacing:{after:60}, children:[new TextRun(t)] });

const border = { style: BorderStyle.SINGLE, size: 1, color: "BBBBBB" };
const borders = { top:border, bottom:border, left:border, right:border, insideHorizontal:border, insideVertical:border };
function cell(text, w, {head=false, bold=false, align=AlignmentType.LEFT}={}) {
  return new TableCell({ borders, width:{size:w,type:WidthType.DXA},
    shading: head?{fill:"1F3864",type:ShadingType.CLEAR}:{fill:"FFFFFF",type:ShadingType.CLEAR},
    margins:{top:60,bottom:60,left:100,right:100},
    children:[new Paragraph({ alignment:align, children:[new TextRun({text:String(text), bold:head||bold, color:head?"FFFFFF":"000000", size:19})]})] });
}
function table(rows, widths) {
  return new Table({ width:{size:CW,type:WidthType.DXA}, columnWidths:widths,
    rows: rows.map((r,i)=> new TableRow({ tableHeader:i===0,
      children: r.map((c,j)=> cell(c, widths[j], {head:i===0, align: j===0?AlignmentType.LEFT:AlignmentType.CENTER})) })) });
}

const doc = new Document({
  creator:"Global Market Scanners", title:"A Reproducible Multi-Market Equity-Factor Platform",
  styles:{ default:{document:{run:{font:"Arial",size:21}}},
    paragraphStyles:[
      {id:"Heading1",name:"Heading 1",basedOn:"Normal",next:"Normal",quickFormat:true,
        run:{size:26,bold:true,color:"1F3864",font:"Arial"},paragraph:{spacing:{before:280,after:140},outlineLevel:0}},
      {id:"Heading2",name:"Heading 2",basedOn:"Normal",next:"Normal",quickFormat:true,
        run:{size:22,bold:true,color:"2E5090",font:"Arial"},paragraph:{spacing:{before:200,after:100},outlineLevel:1}},
    ]},
  numbering:{config:[
    {reference:"bul",levels:[{level:0,format:LevelFormat.BULLET,text:"•",alignment:AlignmentType.LEFT,style:{paragraph:{indent:{left:600,hanging:280}}}}]},
    {reference:"num",levels:[{level:0,format:LevelFormat.DECIMAL,text:"%1.",alignment:AlignmentType.LEFT,style:{paragraph:{indent:{left:600,hanging:300}}}}]},
    {reference:"refs",levels:[{level:0,format:LevelFormat.BULLET,text:"",alignment:AlignmentType.LEFT,style:{paragraph:{indent:{left:400,hanging:400}}}}]},
  ]},
  sections:[{
    properties:{ page:{ size:{width:12240,height:15840}, margin:{top:1440,right:1440,bottom:1440,left:1440} } },
    footers:{ default: new Footer({ children:[ new Paragraph({ alignment:AlignmentType.CENTER,
      children:[ new TextRun({text:"Global Market Scanners — Working Paper v1.0    ·    Page ",size:16,color:"888888"}),
                 new TextRun({children:[PageNumber.CURRENT],size:16,color:"888888"}) ]}) ]}) },
    children:[
      // ---- Title block ----
      new Paragraph({spacing:{before:1200,after:120}, alignment:AlignmentType.CENTER,
        children:[new TextRun({text:"A Reproducible Multi-Market Equity-Factor Platform",bold:true,size:40,color:"1F3864"})]}),
      new Paragraph({alignment:AlignmentType.CENTER, spacing:{after:300},
        children:[new TextRun({text:"Construction, Cross-Sectional Evidence, and the Primacy of Measurement",italics:true,size:26,color:"2E5090"})]}),
      new Paragraph({alignment:AlignmentType.CENTER, spacing:{after:60}, children:[new TextRun({text:"Global Market Scanners project — Working Paper v1.0",size:22})]}),
      new Paragraph({alignment:AlignmentType.CENTER, spacing:{after:60}, children:[new TextRun({text:"Generated 3 July 2026 · commit-signed · CI-verified (102 tests)",size:18,color:"666666"})]}),
      new Paragraph({alignment:AlignmentType.CENTER, spacing:{after:400}, children:[new TextRun({text:"Not investment advice — research only",size:16,italics:true,color:"888888"})]}),
      // ---- Abstract ----
      new Paragraph({ border:{ top:{style:BorderStyle.SINGLE,size:6,color:"1F3864",space:8}, bottom:{style:BorderStyle.SINGLE,size:6,color:"1F3864",space:8} },
        spacing:{before:200,after:120}, children:[new TextRun({text:"Abstract",bold:true,size:24,color:"1F3864"})]}),
      P([new TextRun("We build and openly document a reproducible, multi-market equity-research platform (~40 Python modules, 19 markets, 102 CI-gated unit tests) and use it to test a battery of classic and modern cross-sectional signals under a common, look-ahead-free protocol. Rather than mine for alpha, we ask a narrower and more answerable question: "),
         new TextRun({text:"which signals survive careful measurement, and what does “careful” cost?",italics:true}),
         new TextRun(" We implement Quality-Minus-Junk (Asness-Frazzini-Pedersen; Jacob-Pradeep-Varma 2022), post-earnings-announcement drift (PEAD), the Amihud (2002) illiquidity premium, an OBV/Chaikin-money-flow accumulation screen, seasonality, crowding, and price-based microstructure studies. Four results stand out.")]),
      li("An accumulation/CMF signal is weak at one month but shows a monotone +10.8% top-minus-bottom spread at six months."),
      li("PEAD is invisible under a volume-spike event proxy but appears once events are dated with real SEC EDGAR filing dates (US liquidity-conditioning information coefficient rises 10×, from +0.010 to +0.102); cross-country it concentrates in less-efficient/emerging markets (Brazil +0.24; the US ≈ 0)."),
      li("The liquidity premium is undetectable in a single one-year cross-section but is significant in a ten-year Fama-MacBeth test (+4.24% per quarter, t = 2.16)."),
      li("Our quality tilt loads negatively on value (−0.88) and positively on momentum (+0.36) against the real Kenneth-French factors, matching the literature."),
      P([new TextRun("The unifying finding is methodological: in three of four cases the signal existed but was hidden by measurement — a noisy event proxy, a pooled average, or a sample too short for power. "),
         new TextRun({text:"Measurement quality, not data quantity, was the binding constraint.",bold:true})]),
      P([new TextRun({text:"Keywords: ",bold:true}), new TextRun({text:"factor investing, PEAD, liquidity premium, quality (QMJ), reproducible research, point-in-time backtesting, cross-market equities.",italics:true})]),
      new Paragraph({children:[new PageBreak()]}),
      new Paragraph({heading:HeadingLevel.HEADING_1, children:[new TextRun("Contents")]}),
      new TableOfContents("Contents",{hyperlink:true,headingStyleRange:"1-2"}),
      new Paragraph({children:[new PageBreak()]}),

      // ---- 1 ----
      H("1. Introduction", HeadingLevel.HEADING_1),
      P("Empirical asset pricing is plagued by a replication crisis: many published anomalies fail out of sample or under more careful measurement. Our contribution is not a new anomaly but a reproducible apparatus — every signal is a small, unit-tested pure function; every result is regenerable from committed code under continuous integration; and the platform is governed (TOGAF architecture checks), planned (SAFe backlog), and integrity-verified (SHA-256 manifest, signed commits)."),
      P("Three contributions: (1) an open, tested platform spanning quality, momentum, value, low-risk, liquidity, PEAD, seasonality, crowding, sentiment, options-implied and ESG signals, plus a decision layer and a versioned write surface; (2) cross-market evidence under one protocol, including a per-country decomposition of PEAD's liquidity conditioning and a multi-year liquidity-premium test; and (3) a methodological thesis — that measurement quality dominates data quantity."),

      // ---- 2 ----
      H("2. Data", HeadingLevel.HEADING_1),
      P("The platform draws only on free, public sources:"),
      table([
        ["Source","Content","Coverage"],
        ["Yahoo Finance","Daily OHLCV","19 markets; ~1y cached + 10y US fetch"],
        ["SEC EDGAR","XBRL fundamentals + 10-Q/10-K filing dates + 8-K","US (point-in-time)"],
        ["Fundamentals snapshot","ROE/ROA/D-E/growth/margin/yield","~731 firms (current snapshot)"],
        ["Kenneth French Library","Real FF5 + Momentum + RF","US/regional, 1963-2026"],
        ["OpenAlex/Crossref/arXiv","Scholarly metadata (scout)","Global"],
      ], [1900, 3600, 3860]),
      P([new TextRun({text:"Coverage caveats (material to the results). ",bold:true}),
         new TextRun("(a) The cached history is ~1 trading year, limiting long-horizon and monthly tests; the liquidity-premium test uses a dedicated 10-year fetch. (b) Seven markets (CH, CN, DK, HK, JP, KR, TW) carry <250 trading days, so liquidity-gated screeners return no universe there. (c) Fundamentals are a current snapshot, not a filed-date panel outside the US. (d) India — the source quality paper's market — is not in the cache; QMJ is generalised to the markets present.")], {spacing:{before:100,after:120}}),

      // ---- 3 ----
      H("3. Platform and methodology", HeadingLevel.HEADING_1),
      H("3.1 Architecture", HeadingLevel.HEADING_2),
      P("Shared data-plumbing and cross-sectional statistics live in one library (marketdata.py). Each factor is a module of small pure functions plus a CLI, with domain logic separated from plumbing. Reproducibility is enforced by 102 unit tests, an architecture-governance check (10/10 principles), and a file-integrity manifest, all run in CI on every push."),
      H("3.2 Validation protocol", HeadingLevel.HEADING_2),
      P("All tests are point-in-time: features are measured over a window ending at date T, returns realised strictly after T. We report quantile forward returns, the information coefficient (IC = corr(signal, forward return)), and rank monotonicity; the liquidity premium uses a Fama-MacBeth procedure (sort each period, average quintile returns across periods, t-test the spread). Quality loadings are estimated by calendar-time regression on the real Kenneth-French factors. Returns are gross; costs are modelled separately."),

      // ---- 4 ----
      H("4. Results", HeadingLevel.HEADING_1),
      H("4.1 Quality (QMJ)", HeadingLevel.HEADING_2),
      P("Against real Kenneth-French Carhart factors, the long-only quality portfolio loads HML −0.88 (t = −4.3) and Mom +0.36 (t = 2.8) — quality is not cheap and has momentum, matching Asness et al. (2019) and Jacob et al. (2022). The cross-sectional price premium is positive and profitability-driven (quality coefficient +0.058), though weaker than the paper's 26-year panel because our fundamentals are a single snapshot."),
      H("4.2 Accumulation / Chaikin Money Flow", HeadingLevel.HEADING_2),
      table([
        ["Horizon / signal","Q5−Q1 fwd","Monotonicity","IC"],
        ["1-month · CMF","+1.25%","+0.70","+0.018"],
        ["6-month · CMF","+7.80%","+0.90","+0.059"],
        ["6-month · accumulation","+10.82%","+1.00","+0.071"],
      ], [3360, 2000, 2000, 2000]),
      P("Accumulation is a multi-month signal: near-zero at one month, monotone and economically large at six — the strongest tradeable result in the platform.", {spacing:{before:100,after:120}}),
      H("4.3 Post-earnings-announcement drift (PEAD)", HeadingLevel.HEADING_2),
      P("Under a volume-spike event proxy the US liquidity-conditioning IC is ≈ 0 (+0.010). With real SEC EDGAR 10-Q/10-K filing dates (313 events) it rises to +0.102 — a 10× improvement — because real dates strip the proxy's noise. Cross-country, the Chordia-Sadka prediction holds in 8 of 12 markets, strongest in Brazil (+0.24) and ≈ 0 in the efficient US (+0.01). Announcement-day volume surges ~3.8× (proxy) and ~1.6× (true filing date)."),
      H("4.4 Liquidity premium — the power of a longer sample", HeadingLevel.HEADING_2),
      P("A single one-year cross-section gives a weak, non-monotone premium. A dedicated ten-year Fama-MacBeth test (199 US names, 35 quarterly cross-sections, 5,723 stock-observations) is decisive:"),
      table([
        ["63-day forward","Q1 liquid","Q2","Q3","Q4","Q5 illiquid"],
        ["mean return","+4.16%","+2.62%","+4.54%","+3.32%","+8.40%"],
      ], [2360, 1400, 1400, 1400, 1400, 1400]),
      P([new TextRun({text:"Q5−Q1 = +4.24% per quarter, t = 2.16 (|t|>2), avg IC +0.055",bold:true}),
         new TextRun(" — a statistically significant liquidity premium (Amihud-Mendelson 1986; Amihud 2002). The one-year result was under-powered, not wrong.")], {spacing:{before:100,after:120}}),
      H("4.5 Other signals and the cross-market snapshot", HeadingLevel.HEADING_2),
      P("The turn-of-the-month effect is present (US edge +0.22%); monthly/Halloween effects require multi-year data and are withheld. Fundamental-quality “Strong Performer” counts are highest in Singapore (20), Brazil (19), South Africa (16) and the UK (15) and lowest in the US (3) — the same emerging-market tilt PEAD exhibits — while the US dominates technical activity (2,833 Darvas coils vs <800 elsewhere), reflecting market depth."),

      // ---- 5 ----
      H("5. Assumptions and limitations", HeadingLevel.HEADING_1),
      li("Snapshot fundamentals outside the US — quality/valuation results are contemporaneous, not point-in-time; magnitudes are indicative.","num"),
      li("Event proxies — where real dates are unavailable, earnings events are proxied by volume/return spikes, which conflate earnings with other news.","num"),
      li("Short cache (~1 trading year) — long-horizon, monthly, and single-market PEAD tests are under-powered; the liquidity test uses a separate 10-year fetch.","num"),
      li("Survivorship — universes are current liquid names; the 10-year liquidity test inherits mild survivorship bias.","num"),
      li("Gross returns — reported spreads are pre-cost; the strongest signals survive modest costs, marginal ones do not.","num"),
      li("Data-provider quality — yfinance options/sustainability endpoints are inconsistent; affected modules are validated on pure logic, not live data.","num"),
      li("Not investment advice — all outputs are research signals.","num"),

      // ---- 6 ----
      H("6. Reproducibility and governance", HeadingLevel.HEADING_1),
      P("Every figure regenerates from committed code. The platform enforces 102 unit tests over deterministic cores; architecture governance (10/10 principles) failing CI on regression; a SHA-256 integrity manifest and SSH-signed commits; a SAFe backlog (77 features) mapping work to strategic themes; and a literature scout that keeps the implemented-vs-frontier map current (zero open gaps). Large data is content-addressed via Git LFS; a versioned CRUD store provides an auditable write surface. This machinery is the paper's real subject: it is what lets the empirical claims be checked rather than trusted."),

      // ---- 7 ----
      H("7. Conclusion", HeadingLevel.HEADING_1),
      P("Across quality, drift, and liquidity, the recurring lesson is that the binding constraint was measurement, not the signal. PEAD emerged only with real filing dates; its cross-country structure emerged only when we stopped pooling; the liquidity premium emerged only with a decade of data. Each careful re-measurement turned an inconclusive or null result into a significant, theory-consistent one — the inverse of the replication crisis, where careless measurement turns noise into “anomalies.” A reproducible, tested, governed platform is not bureaucratic overhead; it is the precondition for trusting any of these conclusions."),

      // ---- References ----
      H("References (methods implemented)", HeadingLevel.HEADING_1),
      ...["Amihud, Y. (2002). Illiquidity and stock returns. Journal of Financial Markets.",
          "Amihud, Y., & Mendelson, H. (1986). Asset pricing and the bid-ask spread. JFE.",
          "Asness, C., Frazzini, A., & Pedersen, L. (2019). Quality minus junk. Review of Accounting Studies.",
          "Bernard, V., & Thomas, J. (1989/1990). Post-earnings-announcement drift.",
          "Chordia, T., Goyal, A., Sadka, G., Sadka, R., & Shivakumar, L. (2009). Liquidity and PEAD.",
          "Cohen, L., & Frazzini, A. (2008). Economic links and predictable returns. Journal of Finance.",
          "Corwin, S., & Schultz, P. (2012). A high-low spread estimator. Journal of Finance.",
          "Fama, E., & French, K. (1992, 1993, 2015). Cross-section; three- and five-factor models.",
          "Fama, E., & MacBeth, J. (1973). Risk, return, and equilibrium. JPE.",
          "Frazzini, A., & Pedersen, L. (2014). Betting against beta. JFE.",
          "Gu, S., Kelly, B., & Xiu, D. (2020). Empirical asset pricing via machine learning. RFS.",
          "Jacob, J., Pradeep, K.P., & Varma, J. (2022). Performance of quality factor in Indian equity market. IIMA W.P. 2022-11-01.",
          "Jegadeesh, N., & Titman, S. (1993). Returns to buying winners and selling losers. Journal of Finance.",
          "Loughran, T., & McDonald, B. (2011). When is a liability not a liability? Textual analysis. Journal of Finance.",
          "Markowitz, H. (1952). Portfolio selection. Journal of Finance.",
          "Novy-Marx, R. (2013). The other side of value: the gross profitability premium. JFE.",
          "Sharpe, W. (1964). Capital asset prices. Journal of Finance."].map(r=> li(r,"refs")),
    ]
  }]
});
Packer.toBuffer(doc).then(b=>{ fs.writeFileSync("/Users/umashankar/global-market-scanners/RESEARCH_PAPER.docx", b); console.log("wrote RESEARCH_PAPER.docx", b.length, "bytes"); });
