# Materials-Triage

## Agent-coding setup

This repo was built with Claude Code, and the `.claude/` directory is configured to showcase that workflow — custom slash commands, skills, a status line, and permission settings.

See **[`.claude/README.md`](.claude/README.md)** for a full tour. Highlights:

- **`/deep-plan`** — multi-round planning pass (think → plan → think harder → refine → think hardest → finalize) pinned to Opus.
- **`/handoff`** — structured session handoff docs so a fresh session resumes with zero re-discovery.
- **`verify-skill-package`** skill — audits third-party skills/npm packages for malicious code before running them.
- **Custom status line** + permission allow/deny lists (the global `deny` list hard-blocks `rm -rf`, `sudo`, `curl|bash`, and secret-file reads).
