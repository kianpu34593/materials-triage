---
name: verify-skill-package
description: Audit a third-party agent skill or npm package for malicious code before installing or running it. Use when the user is about to run `npx skills add ...`, `npx <pkg>`, install a skill from a GitHub repo, or asks "is this safe to install / run", "check this package/skill for malware", or wants to vet supply-chain risk before trusting third-party code an agent will execute.
argument-hint: <repo (owner/name) or npm package, optionally --skill <name>>
---

# Verify a third-party skill or package

Goal: decide whether a skill/package is safe **before** any of its code runs. Two trust boundaries matter:

1. **Install/run time** — `npx <pkg>` *executes immediately*; npm `preinstall`/`postinstall`/`prepare` hooks run on install. This is code on the user's machine with the user's privileges.
2. **Invocation time** — a skill's `SKILL.md` is plain-text instructions loaded into the agent's context. Even with zero scripts it can steer the agent (prompt injection). Bundled scripts run later via the agent's own shell tool.

So "no code" ≠ "harmless." Always read both the scripts **and** the SKILL.md.

## Rules

- **Never** run `npx <pkg>` or `npm install` to inspect — that executes it. Clone/fetch read-only first.
- A manual review catches obvious issues but is **not a guarantee**: obfuscated code or runtime-only misbehavior can evade a read-through. For anything uncertain, run in a sandbox/VM with no credentials mounted.
- Report findings plainly. If you can't verify something, say so.

## Procedure

### Step 0 — Fetch read-only (do NOT execute)
```bash
git clone --depth 1 https://github.com/<owner>/<repo> /tmp/<repo>-audit
```

### Step 1 — Map every file
```bash
find /tmp/<repo>-audit -type f -not -path '*/.git/*' | sort
```
Suspicious: minified/obfuscated JS, unexpected binaries, base64 blobs, scripts doing network I/O.

### Step 2 — Install-time hooks (highest risk; runs before invocation)
```bash
grep -rn 'preinstall\|postinstall\|"prepare"' --include='*.json' /tmp/<repo>-audit | grep -v node_modules
ls -la /tmp/<repo>-audit/.npmrc 2>/dev/null
cat /tmp/<repo>-audit/package.json   # inspect "scripts" and "bin"
```
`prepack`/`prepublish` run at *publish*, not on the user's install — lower concern.

### Step 3 — Read the SKILL.md (prompt-injection surface)
```bash
cat /tmp/<repo>-audit/**/SKILL.md
```
Red flags: instructions to read `~/.ssh`, `.env`, credentials/tokens; exfiltrate via curl/POST; `eval`; "ignore previous instructions"; disable safety checks.

### Step 4 — Grep source for dangerous behavior, then READ each hit in context
```bash
grep -rniE 'curl|wget|child_process|execSync|spawn|eval\(|new Function|atob|base64|\.ssh|\.env|credential|api[_-]?key|secret|token|/etc/passwd|rm -rf|chmod|fetch\(|https?://' \
  --include='*.js' --include='*.mjs' --include='*.cjs' --include='*.ts' --include='*.sh' --include='*.py' \
  /tmp/<repo>-audit/src /tmp/<repo>-audit/scripts /tmp/<repo>-audit/bin | grep -viE 'test|spec'
```
Grep finds candidates; you judge intent. Catalog every external host and every env var read.

### Step 5 — Provenance
- Is the author/account established? Repo age, stars, commit history, CI, tests, license?
- Who publishes the CLI/package? `npm view <pkg>` — same author as the repo?
- Brand-new account + brand-new repo + "just run this command" = classic supply-chain setup.

### Step 6 — npm tarball ≠ GitHub source (the published code is what actually runs)
The npm package often ships a **bundled** build, not the readable `src/`. Verify the published artifact:
```bash
cd /tmp && mkdir npmpack && cd npmpack && npm pack <pkg>
tar tzf <pkg>-*.tgz | sort                 # what's shipped
tar xzf <pkg>-*.tgz
diff package/**/SKILL.md /tmp/<repo>-audit/**/SKILL.md   # SKILL.md should match
grep -oiE 'https?://[a-z0-9._/-]+' package/dist/*.mjs | sort -u   # only expected hosts?
grep -onE 'eval\(|new Function|child_process|atob\(' package/dist/*.mjs   # surprises?
```
Confirm shipped hosts/behaviors match the source you reviewed. A version lag where npm is *behind* GitHub is normal and fine.

## Verdict template
Report: install hooks (yes/no), dangerous patterns found (+context), external hosts contacted, env vars read, telemetry (present? on by default? opt-out?), SKILL.md injection risk, npm-vs-source match, provenance. End with a plain safe / unsafe / safe-with-caveats call and any hardening env vars.
