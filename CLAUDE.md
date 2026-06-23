# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this repo is

**Materials-Triage** — a public-data-only agent that turns a scientist's natural-language
request into a **ranked, fully-cited shortlist of candidate materials**, with caveats and
clearly-marked missing/uncertain data, in two views: a concise **PI summary** and a detailed
technical **audit** view. It never triggers wet-lab actions, reads private lab data, or scrapes
paywalled sources, and resists basic prompt-injection by construction.

**Load-bearing decision:** the LLM never invents scientific facts. Public databases supply
every number (tagged with provenance), deterministic code filters and ranks, and the LLM only
builds the spec, proposes hypotheses, and writes grounded, cited narrative. This yields three
properties: **traceable** (every run is a recorded, replayable `TriageRun`), **configurable**
(tweak a knob, resume from that step), and **generalizable with zero setup** (no mandatory
profile — the spec is LLM-built and remembered in lab memory).

**Workflow (traced state machine, not an autonomous tool-calling loop):**

1. **Input policy gate** — allowlist; forbidden/out-of-scope → logged refusal (not recorded).
2. **Spec building (LLM + human)** — Convert language → Filling Spec (`TriageSpec`); on missing
   fields, Recommend LLM (seeded by built-in defaults + lab memory) → user accepts or fills in
   (manual fills pass a user-input gate) → Final Spec.
3. **Hypothesis (LLM + RAG)** — propose candidates / property ranges from literature; hypotheses, not facts.
4. **Retrieve (code, not LLM)** — deterministic API calls; **v1: Materials Project only**. Returns
   candidates + `PropertyValue`s, each carrying `Provenance`.
5. **Hard filters** — drop any candidate violating any hard constraint; each drop records a reason.
6. **Ranking** — one of two selectable strategies, recorded per run on `TriageSpec.ranking_method`:
   `geometric_mean` (the agent default — what `compile_spec` emits — a non-compensatory weighted
   geometric mean of per-target Derringer–Suich desirabilities, where one zero desirability zeros
   the score; it requires every ranking target to announce explicit desirability ramp bounds) or
   `arithmetic_mean` (compensatory weighted average of target properties; still the `TriageSpec`
   field default). Applies `on_missing` and flags missing data.
7. **Synthesis (LLM + RAG)** — grounded narrative + mechanistic "why," each claim cited; no invented numbers.
8. **Output validator** — every referenced ID + citation must resolve to retrieved provenance; ungrounded → reject & retry.
9. **Render** — `view=pi` concise summary · `view=audit` renders the full trace.

Cross-cutting: a **literature RAG** (BM25 over OpenAlex/Crossref abstracts, treated as untrusted
DATA) feeds steps 3 & 7; two persistence buckets — **lab memory** (saved specs) and **`TriageRun`**
(per-run trace) — and the trace saves back to memory.

**Locked decisions:** vertical slice (one real source + stubs + design note) · first source
Materials Project · LLM = Claude on **AWS Bedrock** (IAM creds, no Anthropic key; mockable for
offline eval) · installable package + CLI · literature = metadata/abstracts only (no full text) ·
RAG = lexical BM25, keyless, per-query in-memory · execution = traced state machine · re-runs via
automatic step-cache + `resume --from`. No mandatory profile; cross-source merge deferred;
standalone caveats stage deleted (missing flags are a byproduct of ranking).

**Why it's safe & honest:** facts come from tools not the LLM (hallucination is structurally
impossible in the numeric layer) · capability-by-construction (no wet-lab/private-DB/scraper tool
exists) · retrieved text is untrusted DATA never instructions · output validator enforces
resolvable IDs/citations · missing data is first-class (ranked-but-flagged, never silently
dropped or guessed) · no DB to host (HTTP client over public APIs; only local state is run traces
+ memory).

Full design lives in `Deep-Plan-materials-triage-agent-2026-06-19-1429.md` (§0 has the
workflow diagram). For **current build status and the next-steps plan, see
[`docs/handoff.md`](docs/handoff.md)** — that is the single source of truth for what's
merged vs. pending; this file stays focused on durable design + how-to-work guidance.

Package layout (monorepo):
- `src/materials_triage/core/` — frozen domain models (`schema.py`, `elements.py`),
  deterministic logic (`scoring.py`, `ranking.py`), hypothesis layer (`hypothesis.py`),
  synthesis artifact (`synthesis.py`: `GroundedClaim`/`Synthesis` + the
  `ungrounded_record_ids` grounding check shared by the validator and the synthesis
  retry loop), audit-trace export (`run_trace.py`). Pure, no heavy deps.
- `src/materials_triage/sources/` — `SourceAdapter` + the Materials Project adapter
  (injected `http_get`, lazy `requests`). The adapter exposes `property_vocabulary()`
  — its queryable property→unit surface — derived from the committed, generated
  `_mp_fields.py` table (`MP_FIELDS`: units + XC-functional origins). That module
  also carries `PUSHABLE_PARAMS` — the distinct, larger `/summary` GET query-param
  surface; the adapter pushes every hard filter MP can express server-side (numeric
  bounds, booleans, element all/none, element count), gating each on that set and
  acting as the single authority for what it pushes. The adapter also exposes
  `classify_predicates(spec) -> PredicateRouting`, routing each hard predicate against
  those two surfaces: retrievable-but-not-queryable ones (the *exclusive set*, e.g.
  `is_magnetic`, element `any`) go to local buckets that `core/scoring.py`'s
  `apply_local_filters` enforces, and predicates the source can neither push nor return
  go to loud run-level `caveats`. `retrieval/rag.py` — BM25 literature RAG.
- `src/materials_triage/agent/` — Bedrock `HypothesisProvider`/`SynthesisProvider`
  (`llm.py`; the former also exposes `extract_keywords` for the RAG step), prompts
  (`prompts.py`: `ROLE_SYSTEM_PROMPT`, `build_chat_messages`, `build_hypothesis_prompt`
  and `build_synthesis_prompt` — trusted shortlist/vocabulary as instruction text, user
  goal + RAG snippets fenced as untrusted DATA), the output validator (`validator.py`: `validate_output` raises
  `UngroundedOutputError` unless every presented candidate and narrative citation
  resolves to retrieved provenance), LangGraph `orchestrator.py` (9-step linear graph +
  checkpointer). `policy/guardrails.py` — input gate + trust-boundary wrapper.
  `memory/store.py` — lab memory.
- `server/` — public-web-app hosting layer; imports the pure core, never the reverse.
- `tools/` — dev-only generators, never part of the runtime package (on the test
  pythonpath only): `gen_mp_vocab.py` parses the vendored MP OpenAPI snapshot
  (`mp_summary_schema.json`) into the committed `sources/_mp_fields.py` module —
  both the `MP_FIELDS` table and the `PUSHABLE_PARAMS` query-param set.
- Heavy deps (`langchain-aws`, `requests`) live at the edges behind optional extras +
  lazy imports; the live Bedrock/MP/OpenAlex tests are `live`-marked (deselected in CI).

The repo's agent-coding setup (commands, skills, settings) is in [`.claude/README.md`](.claude/README.md).

## How to work here (collaboration rules)

These override default behavior — follow them exactly.

1. **Ask before choosing between approaches.** When there's more than one reasonable
   way to implement something (design, library, data structure, API shape), stop and
   ask which direction I want — don't pick one and run with it. Present the options
   with a short recommendation.

2. **Implement one function at a time.** Write a single function, then **stop and get
   my approval before moving to the next one.** Do not batch multiple functions or
   build out a whole module in one pass.

3. **TDD preferred — use the `tdd` skill.** Default to test-driven development via the
   `tdd` skill (red-green-refactor). Work in **vertical slices**: one failing test →
   minimal code to pass → repeat — never write all tests up front (that produces tests
   of imagined behavior). Test observable behavior through public interfaces, not
   implementation details. Confirm tests pass before asking to proceed. (The
   `tdd-test-writer` agent can also help author tests.)

## Git workflow

- **`main` is protected.** It requires **signed commits** (SSH signing is configured
  local to this repo) and passing branch-protection checks. Never push directly to
  `main`.
- **Commit signing is mandatory.** This repo signs commits with **SSH** (key
  `~/.ssh/id_ed25519.pub`), configured local to the repo: `gpg.format=ssh` selects
  the SSH method, and `commit.gpgsign=true` turns on auto-signing. (`commit.gpgsign`
  is a legacy name meaning "sign commits" — it does **not** imply GPG; `gpg.format`
  is what picks SSH vs GPG. GPG is not used or installed here.) Commits are signed
  automatically; `git commit -S` also works. An unsigned commit is rejected by
  branch protection.
- **To ship changes, use `/commit-commands:commit-push-pr`.** It branches off `main`,
  creates a signed commit, pushes, and opens a PR. Do not hand-roll the
  commit/push/PR sequence.
- Pushing triggers the `ask`-gated `git push` permission rule — expect a confirmation
  prompt; that's intended.
- **Merge PRs via the GitHub web UI, using squash — never merge locally.** A merge done
  in the web UI is created and signed by GitHub (shows "Verified"), which satisfies the
  `main` signed-commit requirement. Merging locally and pushing would require re-signing
  and is disallowed by branch protection anyway. Squash keeps `main` linear (1 PR = 1
  commit).
- **CI is a required check.** The `test` job (ruff + pytest) must pass before a PR can
  merge.
- **Lint locally before pushing — install the pre-commit hook.** `.pre-commit-config.yaml`
  runs the same ruff format + lint checks CI enforces, at commit time. It is **not** active
  until you run `pre-commit install` once per clone (git hooks aren't checked in). Without
  it, a misformatted commit only fails on CI after push. Run `pre-commit run --all-files`
  to check the whole tree manually; bump the pinned `rev` in lockstep with the ruff version.
- **Merge manually after review — auto-merge is intentionally off.** Review the PR and
  its squash commit message in the web UI before clicking merge; CI passing is necessary
  but not sufficient. (Auto-merge is disabled on purpose so the final commit message gets
  a human check.)
- **After a PR merges, run `/sync-main`.** It fetches with prune, checks out `main`,
  fast-forward pulls, and safely deletes the merged branch (`-d` only; stops on
  uncommitted changes or a non-fast-forward pull). This keeps local `main` in sync and
  the branch list clean. Note: this is guidance I follow within a session when I know a
  PR merged — it is not an automatic trigger; nothing runs on the merge event itself.
