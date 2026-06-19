# Claude Code customizations

This `.claude/` directory showcases how I tailor an agent-coding environment. Everything here is real config used during this take-home — copied in so a reviewer can see (and reuse) the setup. Files load by location: `commands/*.md` become `/slash-commands`, `skills/*/SKILL.md` become model-invokable skills, and `settings*.json` control model, permissions, and the status line.

## Slash commands (`commands/`)

| Command | What it does | Why it earns its keep |
|---------|--------------|------------------------|
| `/deep-plan <task>` | Multi-round planning pass — **think → plan → think harder → refine → think hardest → finalize** (Round 1 medium effort, Round 2 high, Round 3 max), pinned to Opus. Saves a `Deep-Plan-<slug>-<timestamp>.md` to the repo root. | Forces the model to adversarially critique its *own* first draft before committing to a plan. Beats one-shot planning on anything cross-cutting. |
| `/handoff [create\|resume\|update] [note]` | Writes/loads/merges a structured session handoff doc (`docs/handoffs/handoff-<timestamp>.md`) capturing task, files, discoveries, status, and next steps from real git state + conversation history. | Lets a fresh session (after `/clear`, a break, or a context-limit compaction) resume with zero re-discovery. `update` appends history rather than overwriting. |
| `/commit`, `/commit-push-pr`, `/clean_gone` | Anthropic's official `commit-commands` plugin (enabled via `settings.json` → `enabledPlugins`). One-shot git workflows: draft a styled commit; branch + commit + push + open a PR with summary and test plan; prune local branches whose remote is gone. | Stock, vetted commands beat hand-rolled git automation. `/commit-push-pr` still hits the `ask`-gated `git push` rule, so the push is confirmed, not silent. |

Both are scoped with `allowed-tools` so they only run the read-only git/inspection commands they need.

## Skills (`skills/`)

| Skill | Trigger | What it does |
|-------|---------|--------------|
| `verify-skill-package` | About to `npx skills add …` / install a third-party skill or npm package, or "is this safe to run?" | Audits a third-party agent skill or package for malicious code **before** any of it runs: read-only clone → file map → install-hook check → SKILL.md prompt-injection review → dangerous-pattern grep → provenance → npm-tarball-vs-source diff. Born out of vetting `lavish-axi` during this build. |

## Status line (`statusline-command.sh`)

Custom `/statusline` script that renders, in one line: user · cwd · git branch with staged/unstaged counts · model · context-window % + token count (yellow→red past 80%) · session cost · 5-hour rate-limit %. Each segment is omitted gracefully when its data is absent. Wired up via `statusLine` in settings.

## Settings

| File | Role |
|------|------|
| `settings.json` | Committed, shared. The full permission policy for anyone working in this repo: **allow** safe read/inspect/test commands (`ls`, `cat`, `grep`, `pytest:*`…), **ask** before mutations (`git commit/push`, `pip/conda install`, `psql`, `dropdb`), and **deny** dangerous ones (`rm -rf`, `git push --force`, `sudo`, `curl\|bash` pipe-to-shell, reads of `.env` / `.ssh` / `.aws` / `*secret*`). |
| `settings.local.json` | Per-project **personal** overrides — **gitignored** (see root `.gitignore`), never committed. Kept empty/local by design; shared policy lives in `settings.json`. |
| `settings.global.json.example` | Reference copy of my global `~/.claude/settings.json` (model, plugins, status line, same `deny` list). Rename to `settings.json` under `~/.claude/` to use. `deny` always wins over allow/ask at every scope, and permission arrays **merge** across global + project. |

> The `statusLine.command` path in the example points at `~/.claude/statusline-command.sh`. The same script is also vendored here in `.claude/statusline-command.sh` so it's visible in the repo.
