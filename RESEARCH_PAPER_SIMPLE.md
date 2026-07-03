# The Stock-Signal Study — Plain-English (Caveman) Edition

**Umashankar Triplicane Dwarakanathan** · Independent Researcher

**A simple companion to the working paper — Global Market Scanners project**
*Version 1.0 · 2026-07-03 · not investment advice, just learning*

> **What this is.** The same study as the full paper, told in small words. Every number and
> every piece of maths is explained the way you'd explain it to a friend at a campfire. If you
> want the formal version with equations, read `RESEARCH_PAPER_DETAILED.md`. Nothing here is a
> tip to buy or sell anything.

---

## The one-sentence idea

**We tested old "tricks" for guessing which stocks go up. Most of the time, when a trick
looked useless, the trick was fine — we were just *measuring it badly*. Fix the measuring, and
the trick works. So: measure carefully beats collect-more-data.**

---

## First, the words (our little dictionary)

These are the "science words" we use. Each one, in plain talk:

- **Stock.** A tiny slice of owning a company. Its price goes up and down every day.
- **Signal / trick.** A clue we compute from a stock's past (its price, how much it traded)
  that we *hope* tells us if it will go up.
- **Return.** How much money you'd make, in percent. +10% means ten percent richer.
- **Bucket sort (quintiles).** Line up every stock from "least of the clue" to "most of the
  clue," then chop the line into **5 equal piles**. Pile 1 = lowest, Pile 5 = highest. Then we
  watch which pile earns more later. If Pile 5 beats Pile 1, the clue is worth something.
- **The gap (Pile 5 minus Pile 1).** How much more the top pile earned than the bottom pile.
  Bigger gap = stronger clue.
- **Staircase score (monotonicity).** Do the 5 piles go up like neat stairs — Pile 1 lowest,
  then 2, 3, 4, then Pile 5 highest? A score of **1.0 = perfect staircase** (every step higher
  than the one before). Perfect stairs mean the clue is steady and trustworthy, not a fluke
  from one weird pile.
- **Guess-grade (the "IC").** A report-card score for a clue, from about −1 to +1. **0 means
  "no better than a coin flip."** Around **+0.05 is already useful** in this game; higher is
  better. It just measures: when the clue said "this stock is better," was it actually better?
- **Is-it-luck test (the "t-stat").** A number that asks *"could this just be random luck?"*
  Rule of thumb: **above 2 means "probably real, not luck."** Below 2 means "might just be
  noise, don't trust it yet."
- **The 10-year re-check (Fama–MacBeth).** Instead of looking once, we look **every 3 months
  for 10 years** and ask "did the pattern show up again?" If it keeps showing up across all
  those checks, it's real. The more times you check, the surer you get.
- **Hard-to-sell stocks (illiquidity).** Some stocks trade a lot (easy to buy/sell — like a
  popular item at a market). Others barely trade (hard to sell without moving the price). The
  hard-to-sell ones are called *illiquid*.
- **Earnings day.** Four times a year a company tells everyone how much money it made. Big news
  day for the stock.
- **The drift (PEAD).** After that earnings news, the price often keeps *slowly walking* in the
  same direction for weeks, instead of jumping all at once. That slow walk is the "drift."
- **Quality company.** A strong, healthy business: makes good profit, not much debt, steady.
- **Quiet buying (accumulation).** When big smart buyers are slowly loading up on a stock, the
  *trading volume* leaves footprints even before the price moves much. We read those footprints.

That's the whole toolbox. Now the story.

---

## What we actually did (simple version)

1. We grabbed **free daily price data** for stocks in **19 countries** (like the US, UK, Japan,
   Brazil, India-style markets, and more).
2. For each old "trick," we wrote a **tiny, tested program** so anyone can re-run it and get the
   exact same answer. (This matters — see the big lesson.)
3. For every trick we did the same fair test: sort stocks into the **5 buckets**, wait, and see
   if the top bucket beat the bottom bucket — using **only information we'd have known at the
   time** (no cheating by peeking at the future).

We looked at four main tricks. Here's what happened.

---

## Finding 1 — Quiet buying works, but slowly

**The question:** if we spot "quiet buying" footprints, do those stocks go up?

**What we found:** after **1 month**, almost nothing (tiny gap). But after **6 months**, the top
bucket beat the bottom bucket by about **+10.8%**, and the buckets made a **perfect staircase
(score 1.0)**.

**Caveman meaning:** quiet buying is like planting seeds. Check the next day — nothing. Come back
in a few months — the plant is tall. The trick isn't broken; it just needs *time*. This was our
strongest result.

---

## Finding 2 — The earnings "drift" was hiding because we used the wrong date

**The question:** after earnings news, does the price keep drifting? And is it stronger in
sleepy, hard-to-trade markets?

**What we found:** when we *guessed* the earnings day using a "big trading day" as a stand-in,
the clue's report-card score was basically **0** (0.010 — useless). But when we used the **real,
official earnings-filing date** from the government's public database, the score jumped **10
times, to 0.102** — now clearly useful. And the drift was **strongest in Brazil (+0.24)** and
**near zero in the US (~0)**.

**Caveman meaning:** we were standing on the wrong day and wondering why we saw nothing. A "big
trading day" isn't always earnings — it could be a merger or some other noise. Once we used the
*correct* day, the pattern appeared. Also: the drift lives in **sleepier markets**; the US is so
fast and crowded that the news gets priced in almost instantly, leaving no slow walk.

---

## Finding 3 — Hard-to-sell stocks pay you extra (but you need a long look)

**The question:** do hard-to-sell (illiquid) stocks reward you for the hassle?

**What we found:** looking at just **one year**, the answer was a shrug — too little data to
tell. But checking **every 3 months for 10 years**, the hard-to-sell bucket earned about
**+4.24% more each quarter** than the easy-to-sell bucket, and the **is-it-luck number was 2.16
(above 2 = real, not luck)**.

Here's the 10-year picture, in plain money terms (average 3-month return per bucket):

| Bucket | 1 (easiest to sell) | 2 | 3 | 4 | 5 (hardest to sell) |
|---|---|---|---|---|---|
| Earned | +4.16% | +2.62% | +4.54% | +3.32% | **+8.40%** |

**Caveman meaning:** the reward for holding annoying, hard-to-sell stocks is real — but you
can't see it in a quick glance. One year is like tasting one spoon of soup and judging the whole
pot. Ten years of tasting, and the flavour is obvious. The extra pay mostly lives in the *very
hardest* bucket (that +8.40%).

---

## Finding 4 — "Quality" companies are strong, but not cheap

**The question:** do strong, healthy companies behave the way the textbooks say?

**What we found:** using the *real, official* research yardsticks, our quality basket was **the
opposite of "cheap" (score −0.88)** and **rode with the winners (momentum, +0.36)**.

**Caveman meaning:** good companies are like good tools — you *pay up* for them, they're not in
the bargain bin. And people keep buying what's already going up, so quality tends to travel with
the current winners. This is exactly what the famous studies say should happen, so our tool is
built right.

---

## The big lesson (the whole point)

Three of our four tricks first looked **dead**. Each came back to life with **one careful fix**:

1. **Use the right day.** The drift appeared only with the *real* earnings date (10× better).
2. **Don't blend everyone together.** The drift was strong in Brazil and zero in the US —
   averaging all countries into one number hid that. Look country by country.
3. **Look long enough.** The hard-to-sell reward needed *ten years*, not one, to show up.

So the enemy was almost never "the trick doesn't work." The enemy was **sloppy measuring** —
wrong day, mushed-together averages, or too short a look. Fix the measuring, and the signal was
there all along.

This is the opposite of a famous problem in finance, where careless measuring makes *fake* tricks
look real. Same coin, both sides: measure badly and you can invent magic that isn't there, or
bury magic that is. The only cure is a **careful, honest, repeatable method.**

---

## Why we built a "boring robot" to do this

Every number in this study comes out of a **small program that anyone can run again** and get the
exact same answer. We wrapped it in **102 automatic checks** that scream if someone breaks
something. That sounds boring, but it's the whole point: you shouldn't have to *trust* us — you
can **re-run it and check**. In a field full of results that don't hold up, "you can check it
yourself" is the most valuable thing we can offer.

---

## What could still be wrong (being honest)

- Outside the US, our "company health" numbers are a **snapshot of today**, not a full history —
  so those results show the right *direction*, but the exact sizes are rough.
- Some countries had **too little data** (less than a year), so we didn't force a number there —
  we left it blank rather than guess.
- All the returns are **before trading costs**. The strong tricks survive costs; the weak ones
  probably don't.
- We only looked at stocks that **still exist today**, which slightly flatters some results.

We say all this out loud because hiding it would be exactly the "sloppy measuring" we're warning
against.

---

## The tiny takeaway

**Measure carefully. Wait long enough. Don't blend apples with oranges. Then the old tricks tell
the truth.** And whatever you do — this is a study, **not advice to buy or sell anything.**

---

*The full formal version (with the real equations, tables, and citations) is in
`RESEARCH_PAPER_DETAILED.md`. Code and tests: the Global Market Scanners repository.*
