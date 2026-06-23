# Materials-Triage

A robot helper that finds the **best materials** for a scientist — using only
public data, always showing **where every fact came from**, and **never making
up numbers**.

## How it works (the simple version)

You ask for something in plain words (like *"a strong, lightweight metal that
won't rust"*), and the helper walks through 9 little steps to hand you a ranked,
fully-cited shortlist:

```
1. You ask  ──►  2. Safety guard  ──►  3. Make a wish list
   (normal words)    (is it OK?)          (what do we want?)
                                                 │
3. ... ──────────────────────────────────────────┘
   │
   ▼
4. Read books  ──►  5. Look up REAL  ──►  6. Throw out
   for ideas          facts in the          ones that
   (guesses)          science library       don't fit
                                                 │
6. ... ──────────────────────────────────────────┘
   │
   ▼
7. Give stars  ──►  8. Write the answer  ──►  9. Double-check
   (best first)       + where facts            (no made-up
                       came from                 facts!)
                                                 │
                                                 ▼
                                    Show you the list!
                                    (short OR full-detail)
```

**The one big rule:** the robot *never* invents a number. Every value comes from
a public science library (Materials Project), tagged with where it came from.
The AI only helps *understand your wish*, *suggest ideas*, and *write the
explanation* — the real facts and the ranking are done by plain, predictable
code. That's why every answer can be traced, replayed, and trusted.

### What each step does

| Step | Plain words | What's really happening |
|------|-------------|--------------------------|
| 1 | You ask | A natural-language request comes in |
| 2 | Safety guard | An allowlist gate blocks unsafe / out-of-scope asks (no wet-lab, no private data, no paywalled scraping) |
| 3 | Make a wish list | The AI turns your words into a `TriageSpec` (limits + what to rank by); you confirm it |
| 4 | Read books for ideas | The AI reads public paper abstracts (a literature search) and *proposes* candidates — these are guesses, not facts |
| 5 | Look up real facts | Plain code calls the Materials Project API and gets real numbers, each carrying its source |
| 6 | Throw out misfits | Hard filters drop any material that breaks a rule — and record *why* it was dropped |
| 7 | Give stars | A scoring step ranks what's left and flags anything with missing data |
| 8 | Write the answer | The AI writes the explanation, and every claim must point to a real source |
| 9 | Double-check | A validator rejects the answer if any fact can't be traced — then retries |
| → | Show the list | Two views: a short **PI summary** or the full **audit** trace |

Because every run is recorded, you can **replay it**, **tweak one setting and
resume from that step**, and the helper **remembers** past wish lists for next
time.

## Run it (Docker)

Docker is the easiest way to run on any OS — no local Python toolchain needed.

**1. Get credentials**

- **Materials Project** `X_API_KEY` — the public numeric source (required).
- **AWS Bedrock** credentials — the LLM backend (required for live runs): an IAM
  user/role with `bedrock:InvokeModel`, as `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`.
- **OpenAlex** `OPENALEX_MAILTO` — optional; enables the faster literature pool.

```bash
cp .env.example .env      # then fill in the values
```

**2. Check the setup**

```bash
docker compose run --rm triage doctor
```

Prints a ✓/✗ checklist and exits non-zero if a required credential is missing.

**3. Run a triage**

```bash
docker compose run --rm triage \
  "find stable oxide dielectrics for thin films" \
  --runs-dir /data/runs --view audit
```

Run traces are written to `./runs/<run_id>.json` on the host (the `/data/runs`
volume), so you can replay them later.

**Pre-built image.** Pushes to `main` and version tags (`v*`) publish an image to
the GitHub Container Registry, so you can skip the local build:

```bash
docker run --rm --env-file .env -v "$PWD/runs:/data/runs" \
  ghcr.io/kianpu34593/materials-triage:latest doctor
```

> Running without Docker? Install the package (`pip install -e ".[llm]"`) and use
> the `materials-triage` command directly — e.g. `materials-triage doctor`.

## Agent-coding setup

This repo was built with Claude Code, and the `.claude/` directory is configured to showcase that workflow — custom slash commands, skills, a status line, and permission settings.

See **[`.claude/README.md`](.claude/README.md)** for a full tour. Highlights:

- **`/deep-plan`** — multi-round planning pass (think → plan → think harder → refine → think hardest → finalize) pinned to Opus.
- **`/handoff`** — structured session handoff docs so a fresh session resumes with zero re-discovery.
- **`verify-skill-package`** skill — audits third-party skills/npm packages for malicious code before running them.
- **Custom status line** + permission allow/deny lists (the global `deny` list hard-blocks `rm -rf`, `sudo`, `curl|bash`, and secret-file reads).

## Full design

The complete architecture — schema, orchestrator, RAG, and the locked decisions
behind each step — lives in
[`Deep-Plan-materials-triage-agent-2026-06-19-1429.md`](Deep-Plan-materials-triage-agent-2026-06-19-1429.md)
(§0 has the workflow diagram) and the ADRs under [`docs/design/`](docs/design/).
