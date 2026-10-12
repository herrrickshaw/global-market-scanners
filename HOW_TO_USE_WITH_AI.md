# How to use global-market-scanners with an AI assistant

Standalone full-universe equity scanners (Darvas + Piotroski + Coffee Can) across 5 markets — US, Europe, India, Japan, Korea — plus the derived industry/peer parquet dataset

You don't need to be a programmer to use this repository. Get the code,
hand it to an AI assistant (Claude, Gemini, ChatGPT, ...), and ask in plain
language what you want to do.

## 1. Get the code

```bash
git clone https://github.com/herrrickshaw/global-market-scanners.git
cd global-market-scanners
```

No git? Click the green **Code** button on the repo page, choose **Download ZIP**, and unzip it.

You will usually need Python 3.10+ (`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`, if a requirements file exists).

## 2. Give it to an AI

Pick whichever fits you:

| Option | How |
|---|---|
| **Claude Code / Gemini CLI** (best: the AI can read and run the whole repo) | Run `claude` or `gemini` inside the cloned `global-market-scanners` folder, then type your question. |
| **Claude.ai / Gemini / ChatGPT in the browser** | Upload `README.md` and the files you care about (or paste their contents), then ask. For a quick overview, paste the output of `git ls-files` too. |
| **GitHub URL** | Paste `https://github.com/herrrickshaw/global-market-scanners` and ask the AI to read it (works only if your AI tool can browse the web; otherwise upload files). |

Tip: start with the README. If the repo is large, tell the AI which folder or file to focus on instead of pasting everything.

## 3. Sample prompts

Copy, paste, and edit the parts in `[brackets]`.

**Understand it**
- "Read this repo's README and explain in plain English what it does, who it is for, and its main folders."
- "List the main scripts or entry points, and what each one does and needs as input."

**Run it**
- "Give me exact step-by-step commands to set this up on [macOS / Windows / Linux] and run it for the first time. Tell me what output I should expect."
- "I got this error: `[paste error]`. What does it mean and how do I fix it?"

**Use it on your own data**
- "I have [describe your file or question]. Show me which script to run and how to adapt it to my data."
- "Write a short example that calls this code to [do something specific], with comments explaining each line."

**Check and trust it**
- "Where do the numbers or data in this repo come from? Point me to the file and line where each source is read."
- "What assumptions and limitations does this analysis have? What would make its results wrong?"

**Change it**
- "Add [a feature / another country / another column]. Show me the minimal change, and explain it."
- "Write a test for [function name] so I know it still works after I change it."

## 4. Good habits

- **Verify before relying on results.** AI can misread code or invent details. Ask it to cite the file and line it is using, and spot-check important numbers yourself.
- **Never paste secrets** (API keys, passwords, personal data) into an AI tool. If the code needs keys, put them in a local `.env` file that you do not upload.
- **Not financial, legal, medical, or engineering advice.** Outputs are for research and education. Check the README and licence for the repo's specific caveats.
- **Be specific.** Say your operating system, what you have already tried, and the exact error text.

Found a problem or have a question? Open an issue on the repo.
