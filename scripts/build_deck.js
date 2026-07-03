const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.defineLayout({ name:"W", width:13.33, height:7.5 }); p.layout="W";
const NAVY="1E2761", NAVY2="2E3F7E", ICE="CADCFC", WHITE="FFFFFF", AMBER="E8A317",
      SLATE="36454F", MUTE="7A8290", LIGHT="F4F6FB", GREEN="1E7A46", RED="B23A48";
const HF="Cambria", BF="Calibri";
const foot = (s)=> s.addText("Global Market Scanners — Working Paper v1.0    ·    research only, not investment advice",
  {x:0.5,y:7.05,w:12.33,h:0.3,fontFace:BF,fontSize:9,color:MUTE,align:"left"});
const kicker=(s,t,c=NAVY)=>{ s.addShape(p.ShapeType.ellipse,{x:0.5,y:0.52,w:0.16,h:0.16,fill:{color:c}});
  s.addText(t.toUpperCase(),{x:0.72,y:0.42,w:11,h:0.35,fontFace:BF,fontSize:12,bold:true,color:c,charSpacing:2}); };
const title=(s,t)=> s.addText(t,{x:0.5,y:0.78,w:12.33,h:0.9,fontFace:HF,fontSize:32,bold:true,color:NAVY});
function stat(s,x,y,w,num,label,col=NAVY){
  s.addShape(p.ShapeType.roundRect,{x,y,w,h:1.75,fill:{color:LIGHT},line:{color:"E2E7F2",width:1},rectRadius:0.08});
  s.addText(num,{x:x,y:y+0.18,w:w,h:0.9,fontFace:HF,fontSize:40,bold:true,color:col,align:"center"});
  s.addText(label,{x:x+0.15,y:y+1.08,w:w-0.3,h:0.55,fontFace:BF,fontSize:12.5,color:SLATE,align:"center"});
}
const tbl=(s,rows,x,y,w,colW)=> s.addTable(rows,{x,y,w,colW,fontFace:BF,fontSize:12.5,color:SLATE,
  border:{type:"solid",color:"E2E7F2",pt:1},align:"center",valign:"middle",rowH:0.42,
  fill:{color:WHITE}});

// 1 — TITLE (dark)
let s=p.addSlide(); s.background={color:NAVY};
s.addText("A Reproducible Multi-Market\nEquity-Factor Platform",{x:0.7,y:2.0,w:12,h:1.9,fontFace:HF,fontSize:44,bold:true,color:WHITE,lineSpacingMultiple:1.0});
s.addText("Construction, Cross-Sectional Evidence, and the Primacy of Measurement",{x:0.72,y:3.9,w:11.5,h:0.6,fontFace:BF,fontSize:20,italic:true,color:ICE});
s.addShape(p.ShapeType.line,{x:0.75,y:4.7,w:3.2,h:0,line:{color:AMBER,width:2.5}});
s.addText([{text:"19 markets",options:{color:WHITE}},{text:"  ·  ~40 modules  ·  102 CI-gated tests  ·  commit-signed",options:{color:ICE}}],
  {x:0.72,y:4.95,w:11.5,h:0.4,fontFace:BF,fontSize:15,bold:true});
s.addText("Working Paper v1.0 · 3 July 2026 · research only, not investment advice",{x:0.72,y:6.6,w:11.5,h:0.35,fontFace:BF,fontSize:12,color:MUTE});

// 2 — THE QUESTION
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"The question",AMBER); title(s,"Not “what beats the market?” but a sharper one");
s.addText("“Which signals survive careful measurement — and what does careful cost?”",
  {x:0.7,y:2.2,w:12,h:1.1,fontFace:HF,fontSize:28,italic:true,bold:true,color:NAVY});
s.addShape(p.ShapeType.roundRect,{x:0.7,y:3.7,w:12,h:1.7,fill:{color:NAVY},rectRadius:0.1});
s.addText([{text:"The finding, up front:  ",options:{bold:true,color:AMBER}},
  {text:"in 3 of 4 cases the signal was real but hidden by measurement — a noisy event proxy, a pooled average, or too short a sample. ",options:{color:WHITE}},
  {text:"Measurement quality, not data quantity, was the binding constraint.",options:{bold:true,color:WHITE}}],
  {x:1.0,y:3.95,w:11.4,h:1.2,fontFace:BF,fontSize:19,valign:"middle",lineSpacingMultiple:1.1});
foot(s);

// 3 — WHAT WE BUILT
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"The apparatus"); title(s,"A tested, governed research platform — not a black box");
stat(s,0.7,1.9,2.85,"19","markets, one protocol");
stat(s,3.75,1.9,2.85,"~40","pure-function modules");
stat(s,6.8,1.9,2.85,"102","CI-gated unit tests",GREEN);
stat(s,9.85,1.9,2.75,"0","open research gaps");
s.addText("Every signal is a small unit-tested function; every result regenerates from committed code under CI. Governed (TOGAF 10/10), planned (SAFe backlog, 77 features), integrity-verified (SHA-256 manifest + signed commits). Factor library spans quality, momentum, value, low-risk, liquidity, PEAD, seasonality, crowding, sentiment, options-implied and ESG — plus a decision layer and a versioned write surface.",
  {x:0.7,y:4.1,w:12,h:1.9,fontFace:BF,fontSize:16,color:SLATE,lineSpacingMultiple:1.15});
foot(s);

// 4 — DATA & ASSUMPTIONS
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"Data & honesty"); title(s,"Free public data — and the caveats that shape the results");
s.addText("Sources",{x:0.7,y:1.85,w:5.8,h:0.4,fontFace:HF,fontSize:17,bold:true,color:NAVY});
["Yahoo Finance — daily OHLCV, 19 markets (~1y cache + 10y US fetch)","SEC EDGAR — filed-date fundamentals + 10-Q/10-K dates + 8-K","Fundamentals snapshot — ~731 firms (current, not panel)","Kenneth French Library — real FF5 + Momentum, 1963–2026","OpenAlex / Crossref / arXiv — literature scout"].forEach((t,i)=>
  s.addText([{text:"▪  ",options:{color:AMBER,bold:true}},{text:t,options:{color:SLATE}}],{x:0.75,y:2.3+i*0.62,w:5.9,h:0.55,fontFace:BF,fontSize:13.5,valign:"top"}));
s.addShape(p.ShapeType.roundRect,{x:6.9,y:1.85,w:5.8,h:4.5,fill:{color:LIGHT},line:{color:"E2E7F2",width:1},rectRadius:0.08});
s.addText("Caveats (material)",{x:7.15,y:2.0,w:5.4,h:0.4,fontFace:HF,fontSize:17,bold:true,color:RED});
["~1-year cache limits long-horizon/monthly tests → liquidity uses a separate 10-year fetch","7 markets carry <250 trading days → no technical universe there","Fundamentals are a snapshot outside the US (contemporaneous, not point-in-time)","India (the quality paper's market) is absent → QMJ generalised to markets present","Returns are gross; strongest signals survive costs, marginal ones don't"].forEach((t,i)=>
  s.addText([{text:"•  ",options:{color:RED,bold:true}},{text:t,options:{color:SLATE}}],{x:7.2,y:2.5+i*0.72,w:5.35,h:0.68,fontFace:BF,fontSize:12.5,valign:"top"}));
foot(s);

// 5 — ACCUMULATION
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"Result 1 · accumulation / CMF",GREEN); title(s,"A clean multi-month signal");
stat(s,0.7,2.0,3.3,"+10.8%","6-month Q5−Q1 spread",GREEN);
stat(s,0.7,3.95,3.3,"1.00","monotonicity (perfect)",GREEN);
tbl(s,[[{text:"Horizon · signal",options:{bold:true,fill:{color:NAVY},color:WHITE}},{text:"Q5−Q1",options:{bold:true,fill:{color:NAVY},color:WHITE}},{text:"Mono.",options:{bold:true,fill:{color:NAVY},color:WHITE}},{text:"IC",options:{bold:true,fill:{color:NAVY},color:WHITE}}],
  ["1-month · CMF","+1.25%","+0.70","+0.018"],["6-month · CMF","+7.80%","+0.90","+0.059"],
  [{text:"6-month · accumulation",options:{bold:true}},{text:"+10.82%",options:{bold:true}},{text:"+1.00",options:{bold:true}},{text:"+0.071",options:{bold:true}}]],
  4.3,2.15,8.2,[3.4,1.6,1.6,1.6]);
s.addText("Near-zero at one month, monotone and economically large at six — accumulation precedes multi-month moves, not next-week noise. The strongest tradeable result in the platform.",
  {x:4.3,y:4.5,w:8.2,h:1.4,fontFace:BF,fontSize:15,color:SLATE,lineSpacingMultiple:1.15});
foot(s);

// 6 — PEAD
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"Result 2 · PEAD",AMBER); title(s,"The signal was there — the clock was wrong");
s.addShape(p.ShapeType.roundRect,{x:0.7,y:2.0,w:3.7,h:2.0,fill:{color:LIGHT},line:{color:"E2E7F2",width:1},rectRadius:0.08});
s.addText("volume-spike proxy",{x:0.7,y:2.15,w:3.7,h:0.4,fontFace:BF,fontSize:13,color:MUTE,align:"center"});
s.addText("+0.010",{x:0.7,y:2.5,w:3.7,h:0.9,fontFace:HF,fontSize:40,bold:true,color:MUTE,align:"center"});
s.addText("US illiquidity IC ≈ 0",{x:0.7,y:3.45,w:3.7,h:0.4,fontFace:BF,fontSize:12,color:SLATE,align:"center"});
s.addText("➜",{x:4.45,y:2.55,w:0.7,h:0.8,fontFace:BF,fontSize:34,bold:true,color:AMBER,align:"center"});
s.addShape(p.ShapeType.roundRect,{x:5.2,y:2.0,w:3.7,h:2.0,fill:{color:NAVY},rectRadius:0.08});
s.addText("real EDGAR filing dates",{x:5.2,y:2.15,w:3.7,h:0.4,fontFace:BF,fontSize:13,color:ICE,align:"center"});
s.addText("+0.102",{x:5.2,y:2.5,w:3.7,h:0.9,fontFace:HF,fontSize:40,bold:true,color:AMBER,align:"center"});
s.addText("10× stronger (313 events)",{x:5.2,y:3.45,w:3.7,h:0.4,fontFace:BF,fontSize:12,color:WHITE,align:"center"});
stat(s,9.15,2.0,3.4,"8 / 12","markets show the effect",GREEN);
s.addText([{text:"Cross-country it concentrates where theory predicts:  ",options:{color:SLATE}},
  {text:"strongest in emerging Brazil (+0.24), ≈ 0 in the efficient US.  ",options:{bold:true,color:NAVY}},
  {text:"Real dates strip the proxy's noise (M&A, index changes, macro).",options:{color:SLATE}}],
  {x:0.7,y:4.4,w:11.9,h:1.4,fontFace:BF,fontSize:16,lineSpacingMultiple:1.15});
foot(s);

// 7 — LIQUIDITY (chart)
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"Result 3 · liquidity premium"); title(s,"Not wrong at 1 year — under-powered");
s.addChart(p.ChartType.bar,[{name:"63-day fwd return %",labels:["Q1 liquid","Q2","Q3","Q4","Q5 illiquid"],values:[4.16,2.62,4.54,3.32,8.40]}],
  {x:0.7,y:1.95,w:7.4,h:4.4,barDir:"col",chartColors:[NAVY,NAVY,NAVY,NAVY,AMBER],
   showValue:true,dataLabelFormatCode:'0.0"%"',dataLabelFontFace:BF,dataLabelFontSize:11,dataLabelColor:SLATE,dataLabelPosition:"outEnd",
   catAxisLabelFontFace:BF,catAxisLabelFontSize:11,valAxisHidden:true,showLegend:false,barGapWidthPct:60});
stat(s,8.4,2.0,4.2,"+4.24%","per quarter (Q5 − Q1)",GREEN);
s.addShape(p.ShapeType.roundRect,{x:8.4,y:3.95,w:4.2,h:2.15,fill:{color:LIGHT},line:{color:"E2E7F2",width:1},rectRadius:0.08});
s.addText([{text:"10-year Fama–MacBeth\n",options:{bold:true,color:NAVY,fontSize:15}},
  {text:"199 US names · 35 quarterly cross-sections · 5,723 obs\n\n",options:{color:SLATE,fontSize:12.5}},
  {text:"t = 2.16  (significant)   ·   avg IC +0.055",options:{bold:true,color:GREEN,fontSize:14}}],
  {x:8.6,y:4.1,w:3.85,h:1.9,fontFace:BF,valign:"top",lineSpacingMultiple:1.05});
foot(s);

// 8 — QUALITY
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"Result 4 · quality (QMJ)"); title(s,"The right factor DNA vs the real Ken French factors");
stat(s,1.3,2.3,4.6,"−0.88","HML loading — quality is NOT cheap",RED);
stat(s,7.4,2.3,4.6,"+0.36","Mom loading — quality has momentum",GREEN);
s.addText("(t = −4.3)",{x:1.3,y:4.1,w:4.6,h:0.4,fontFace:BF,fontSize:13,italic:true,color:MUTE,align:"center"});
s.addText("(t = +2.8)",{x:7.4,y:4.1,w:4.6,h:0.4,fontFace:BF,fontSize:13,italic:true,color:MUTE,align:"center"});
s.addText("Loadings match Asness-Frazzini-Pedersen and Jacob-Pradeep-Varma (IIMA 2022). The cross-sectional price premium is positive and profitability-driven (weaker than the 26-year panel — our fundamentals are a single snapshot).",
  {x:1.3,y:4.9,w:10.7,h:1.2,fontFace:BF,fontSize:15,color:SLATE,align:"center",lineSpacingMultiple:1.15});
foot(s);

// 9 — CROSS-MARKET
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"Cross-market snapshot",AMBER); title(s,"Fundamental quality tilts emerging; the US runs deep");
s.addChart(p.ChartType.bar,[{name:"“Strong Performer” (GGG) count",labels:["SG","BR","ZA","UK","CA","DE","US","TW"],values:[20,19,16,15,11,9,3,1]}],
  {x:0.7,y:1.95,w:7.6,h:4.4,barDir:"col",chartColors:[AMBER,AMBER,AMBER,AMBER,NAVY,NAVY,RED,NAVY],
   showValue:true,dataLabelFontFace:BF,dataLabelFontSize:11,dataLabelColor:SLATE,dataLabelPosition:"outEnd",
   catAxisLabelFontFace:BF,catAxisLabelFontSize:11,valAxisHidden:true,showLegend:false,barGapWidthPct:55});
s.addText("Highest quality counts: Singapore, Brazil, South Africa, UK.",{x:8.5,y:2.2,w:4.1,h:0.9,fontFace:BF,fontSize:15,bold:true,color:NAVY});
s.addText("Lowest: the US (3) — the most efficiently-priced market, the same tilt PEAD showed.",{x:8.5,y:3.1,w:4.1,h:1.1,fontFace:BF,fontSize:14,color:SLATE});
s.addText("Yet the US dominates technical activity — 2,833 Darvas coils vs <800 elsewhere — reflecting market depth.",{x:8.5,y:4.3,w:4.1,h:1.4,fontFace:BF,fontSize:14,color:SLATE});
foot(s);

// 10 — META LESSON
s=p.addSlide(); s.background={color:WHITE}; kicker(s,"The lesson",AMBER); title(s,"Three nulls that careful re-measurement turned real");
const cards=[["Date the event right","PEAD ≈ 0 under a volume proxy → +0.102 with real SEC filing dates (10×)."],
  ["Stop pooling","A weak pooled PEAD hid a clean per-country pattern: emerging strong, US ≈ 0."],
  ["Wait for the sample","Liquidity premium invisible in 1 year → significant over 10 (t = 2.16)."]];
cards.forEach((c,i)=>{ const x=0.7+i*4.1;
  s.addShape(p.ShapeType.roundRect,{x,y:2.1,w:3.85,h:3.4,fill:{color:LIGHT},line:{color:"E2E7F2",width:1},rectRadius:0.1});
  s.addShape(p.ShapeType.ellipse,{x:x+0.3,y:2.4,w:0.7,h:0.7,fill:{color:NAVY}});
  s.addText(String(i+1),{x:x+0.3,y:2.42,w:0.7,h:0.66,fontFace:HF,fontSize:26,bold:true,color:AMBER,align:"center"});
  s.addText(c[0],{x:x+0.28,y:3.35,w:3.3,h:0.7,fontFace:HF,fontSize:18,bold:true,color:NAVY});
  s.addText(c[1],{x:x+0.28,y:4.05,w:3.3,h:1.3,fontFace:BF,fontSize:14,color:SLATE,lineSpacingMultiple:1.15}); });
foot(s);

// 11 — CLOSE (dark)
s=p.addSlide(); s.background={color:NAVY};
s.addText("Measurement quality,\nnot data quantity.",{x:0.8,y:2.2,w:11.7,h:2.0,fontFace:HF,fontSize:46,bold:true,color:WHITE,lineSpacingMultiple:1.0});
s.addShape(p.ShapeType.line,{x:0.85,y:4.35,w:3.2,h:0,line:{color:AMBER,width:2.5}});
s.addText("The reproducible, tested, governed platform is not overhead — it is the precondition for trusting any of these conclusions.",
  {x:0.85,y:4.6,w:11,h:1.0,fontFace:BF,fontSize:18,italic:true,color:ICE,lineSpacingMultiple:1.1});
s.addText("Code, tests & full paper: Global Market Scanners repository   ·   102 tests · TOGAF 10/10 · signed commits",
  {x:0.85,y:6.5,w:11.6,h:0.4,fontFace:BF,fontSize:12,color:MUTE});

p.writeFile({fileName:"/Users/umashankar/global-market-scanners/RESEARCH_DECK.pptx"}).then(f=>console.log("wrote",f));
