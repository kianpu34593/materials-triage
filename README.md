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

## Using the CLI

The `materials-triage` command has three modes:

```bash
materials-triage doctor                       # environment self-check (✓/✗ checklist)
materials-triage "<goal>" [--view pi|audit]   # one-shot: run a single goal, print the result
materials-triage chat   [--view pi|audit]     # interactive REPL session (below)
```

Common flags on the one-shot and `chat` modes:

- `--view pi` (default) — concise PI summary; `--view audit` — full technical trace.
- `--top-k N` — size of the presented/citable shortlist (the full ranking is still saved).
- `--runs-dir DIR` *(one-shot only)* — persist the run as `DIR/<run_id>.json` for later replay.

### Interactive session (`chat`)

`materials-triage chat` starts a read-eval loop: type a research goal, watch each
workflow step stream by, then approve/edit/regenerate the spec before retrieval runs.

```text
$ materials-triage chat
materials-triage — interactive session
Type a research goal to triage; 'exit' or Ctrl-D to quit.

triage> find stable oxide dielectrics with a wide band gap for thin films
  ✓ gate
  ✓ hypothesis → 4 proposals
Ranking weights were rescaled to sum to 1. Confirm the recommended spec …
{ … recommended TriageSpec as JSON … }
[a]pprove / [e]dit / [r]egenerate / [q]uit: a
  ✓ spec_build → spec confirmed
  ✓ retrieve → 1964 candidates retrieved
  ✓ filter → 1585 survivors, 379 excluded
  ✓ rank → 37 ranked, 1927 excluded
  ✓ synthesis → narrative grounded
  ✓ output_validate
  ✓ render
Goal: find stable oxide dielectrics …

Ranked shortlist:
  1. …
Show full audit trace? [y/N]: n
triage>
```

At the spec gate:

- **`a` approve** — run the workflow to completion with the shown spec.
- **`e` edit** — open the spec as JSON in `$EDITOR`; on save it's re-validated (a bad
  edit is reported and the previous spec kept), then you're back at the menu.
- **`r` regenerate** — re-run the hypothesis step for a fresh proposal, then return to the gate.
- **`q` quit** — abandon this goal and return to the prompt.

After each result you can render the full `audit` trace on request (or start the session
with `--view audit`). An out-of-scope goal is refused with a capabilities note and the
session keeps running; `exit`, `quit`, or Ctrl-D ends it.

> With Docker, add `-it` so the session is interactive:
> `docker compose run --rm -it triage chat`.

### Examples across materials domains

The same workflow generalizes well beyond the canonical "wide-gap oxide" query — the
LLM builds a *different* spec for each goal (picking proxies like `vbm` for voltage or
`is_gap_direct` for a direct gap from the [retrievable vocabulary](#using-the-cli)), the
deterministic gates enforce domain rules (toxic-element exclusion, `is_stable` vs
`energy_above_hull` redundancy), and the per-candidate notes flag materials that match
the numbers but are unsuitable in practice.

Each example below shows the concise **PI view** (`--view pi`); re-run the same goal with
`--view audit` to toggle to the full technical trace (final spec, hypothesis, every
candidate + exclusion reason, and the literature grounding). These are representative
*live* runs (real Bedrock + Materials Project + OpenAlex), so wording and ordering vary
run to run; IDs are from the sandboxed/anonymized MP mirror.

<details>
<summary><b>Li-ion battery cathode</b> — high voltage, stable, transition metals, non-toxic, simple</summary>

```
materials-triage "Find candidate Li-ion battery cathode materials: high operating voltage, thermodynamically stable, containing transition metals, non-toxic elements, and simple compositions. Return a ranked shortlist with caveats." --view pi
```

*Behavior:* built `energy_above_hull ≤ 0.05` + a transition-metal allowlist + a ~30-element
toxic exclusion + `count ≤ 3`, and chose **`vbm` (maximize)** as a high-voltage proxy. Real
layered/spinel cathodes surfaced; the ranking critic raised advisory bound flags on the
under-specified constraints.

```
Ranked shortlist (showing top 6 of 22):
  1. LiNiO2 (mp-aaafdqij) — score 1.00
     Layered LiNiO2 cathode with high voltage potential and nickel redox activity; core layered oxide family.
     ⚠ Structural stability and interfacial degradation during cycling reported as challenges in layered oxide cathodes requiring mitigation strategies.
  2. Li(NiO2)2 (mp-aaabscjl) — score 1.00
     Nickel-based layered oxide with potential for high energy density; simple Li–Ni–O composition.
     ⚠ Layered oxide cathodes face inherent trade-off between structural stability and ion-transport kinetics; mitigation via doping or coating necessary.
  3. LiMn2O4 (mp-aaacukwa) — score 1.00
     Spinel LiMn2O4 with established cathode performance and manganese redox center; recognized in literature as representative LIB cathode.
     ⚠ Structural degradation and metal dissolution known challenges in manganese oxide cathodes; requires protective coating or bulk modification.
  4. LiCrO2 (mp-aaaabbuv) — score 1.00
     Simple ternary LiCrO2 with chromium redox activity; meets composition and transition-metal criteria.
     ⚠ Limited literature prevalence; structural stability and electrochemical cycling behavior require verification for practical cathode application.
  5. LiVO2 (mp-aaaabcpw) — score 1.00
     Layered vanadium oxide with V redox center; simple non-toxic composition.
  6. LiVO3 (mp-aaaabcrd) — score 1.00
     Vanadium oxide cathode with vanadium as redox-active transition metal; simple Li–V–O structure.

Caveats:
  ⚠ Element-level denylist only; toxicity of Ba, Co, Cr, Cu, Mn, Ni, Se, V is oxidation-state / leachability dependent and not resolvable from public DFT data — not auto-excluded.
```
</details>

<details>
<summary><b>OER electrocatalyst</b> — conductive oxide, stable, earth-abundant, non-toxic</summary>

```
materials-triage "Find candidate oxide electrocatalysts for the oxygen evolution reaction (OER): electrically conductive (metallic or very small band gap), thermodynamically stable, earth-abundant and non-toxic elements. Return a ranked shortlist with caveats." --view pi
```

*Behavior:* built `is_metal=True` + `is_stable=True` + a small `band_gap` ceiling + an O
requirement (seeded) + an earth-abundant TM allowlist with a noble/toxic denylist. Every
candidate note honestly flagged the core mismatch — the top oxides lack the transition-metal
d-band framework real OER catalysts need. (Single bounded target → flat scores, see
[#85](https://github.com/kianpu34593/materials-triage/issues/85).)

```
Ranked shortlist (showing top 6 of 543):
  1. AgO (mp-aaagdntk) — score 0.00
     Silver oxide with earth-abundant precursor but unstable and non-conductive.
     ⚠ Thermodynamically unstable; insufficient electrical conductivity for electrocatalysis.
  2. Zr5Sn3O (mp-aaabsjja) — score 0.00
     Sparse intermetallic oxide of Zr and Sn; no experimental OER data.
     ⚠ Low oxygen-site density; lacks transition-metal d-band framework for efficient OER; no published activity metrics.
  3. Sr6Sn2NO (mp-aaacehvg) — score 0.00
     Oxynitride with anomalous N stoichiometry; earth-abundant cations but unvalidated.
     ⚠ Nitrogen-doped oxides are non-standard; no reported OER performance; synthesis and stability uncertain.
  4. Ca6Sn2NO (mp-aaacehwy) — score 0.00
     Calcium–tin oxynitride; earth-abundant but N incorporation is atypical.
     ⚠ Unusual nitrogen stoichiometry; no OER literature; metal–oxygen hybridization unclear.
  5. Ba2NaO (mp-aaacgaeh) — score 0.00
     Binary earth-abundant oxide (Ba, Na) lacking transition metals.
     ⚠ Absence of 3d/4d transition metals eliminates d–p hybridization needed for OER; likely ionic, not conductive.
  6. Eu3InO (mp-aaacivuu) — score 0.00
     Sparse rare-earth oxide with minimal O content.
     ⚠ Lanthanide inclusion raises toxicity concern; extremely low oxygen stoichiometry incompatible with OER; no catalytic precedent.

Caveats:
  ⚠ Element-level denylist only; toxicity of Ba, Co, Cr, Cu, Mn, Ni, Se, V is oxidation-state / leachability dependent and not resolvable from public DFT data — not auto-excluded.
```
</details>

<details>
<summary><b>Photovoltaic absorber</b> — direct gap ~1.3 eV, stable, non-toxic, simple</summary>

```
materials-triage "Find candidate photovoltaic absorber materials: a direct band gap near 1.3 eV, thermodynamically stable, non-toxic elements, simple compositions. Return a ranked shortlist with caveats." --view pi
```

*Behavior:* the hard part landed — `is_gap_direct=True` (hard boolean) **plus a
target-window ranking** on `band_gap` centered at ~1.3 eV (not a min/max). The toxic-element
seeding removed the textbook bad actors (CdTe, PbS, GaAs, Sb₂Te₃), and the notes flagged
wide-gap salts that merely *report* a 1.3 eV gap. Honest about its own limits (a known
spec-expressiveness ceiling).

```
Ranked shortlist (showing top 6 of 402):
  1. HoSF (mp-aaaaaqel) — score 1.00
     Highest-scored compound; holmium sulfide fluoride structure.
     ⚠ Insufficient public data on optoelectronic properties; band gap near 1.3 eV unverified.
  2. Pd(NO3)2 (mp-aaacprgx) — score 1.00
     Salt-phase palladium nitrate.
     ⚠ Inorganic salt; unsuitable for thin-film absorber deposition in photovoltaic devices.
  3. PrVO3 (mp-aaacquyr) — score 1.00
     Perovskite oxide with vanadium; potential direct band gap absorber.
     ⚠ Rare-earth element (Pr); vanadium oxidation state stability and thin-film growth route require experimental confirmation.
  4. DyAgSe2 (mp-aaacpfsw) — score 0.98
     Ternary rare-earth silver selenide; chalcogenide structure.
     ⚠ Contains dysprosium (rare earth); band gap and phase stability under photovoltaic operating conditions unvalidated.
  5. Ag2TeS3 (mp-aaaabrdr) — score 0.98
     Ternary silver chalcogenide; simpler composition than rare-earth alternatives.
     ⚠ Band gap and long-term thermal/chemical stability as thin-film absorber require experimental verification.
  6. CsTeAu (mp-aaabgqtn) — score 0.98
     Intermetallic compound with gold and tellurium.
     ⚠ Gold is precious and environmentally costly; violates non-toxic, simple-composition criterion.
```
</details>

<details>
<summary><b>Solid-state Li electrolyte</b> — contains Li, wide-gap insulator, stable</summary>

```
materials-triage "Find candidate solid-state lithium electrolyte materials: must contain lithium, be a wide-band-gap electronic insulator, and be thermodynamically stable. Return a ranked shortlist with caveats." --view pi
```

*Behavior:* "contains lithium" → an element predicate requiring **Li**; "wide-gap insulator"
→ `band_gap ≥ 4.0` + a maximize ranking; "stable" → `is_stable=True`. The **energetics gate
fired**: it dropped `energy_above_hull` as redundant with the required `is_stable=True`.

```
Ranked shortlist (showing top 6 of 765):
  1. Ba2Li(BO2)5 (mp-aaaaaizr) — score 1.00
     Borate framework with Ba²⁺ stabilizer; suitable for solid electrolyte screening.
  2. Ba2LiAl(CN2)4 (mp-aaacqdwa) — score 1.00
     Carbodiimide-based compound; no experimental ionic conductivity data available; synthesis feasibility not assessed.
  3. BaLi(B3O5)3 (mp-aaaabads) — score 1.00
     Complex polyborate with Li; thermal stability and conductivity unknown from literature.
  4. BaLi2SiO4 (mp-aaabftxm) — score 1.00
     Silicate electrolyte candidate; lithium stoichiometry and transport properties require experimental measurement.
  5. BaLiAlF6 (mp-aaabexii) — score 1.00
     Fluoride-doped aluminate; promising anion-conductivity framework, but no electrochemical data provided.
  6. BaLiBO3 (mp-aaaaajpz) — score 1.00
     Simple borate oxide; structural stability and ionic mobility require validation.

Caveats:
  ⚠ energy_above_hull dropped: redundant with the required is_stable=True (every stable material has energy_above_hull = 0).
```
</details>

<details>
<summary><b>Permanent magnet</b> — magnetic, high magnetization, stable, transition metals</summary>

```
materials-triage "Find candidate permanent-magnet materials: magnetic with high total magnetization, thermodynamically stable, containing transition metals. Return a ranked shortlist with caveats." --view pi
```

*Behavior:* `is_magnetic=True` (enforced by the local filter, since MP can return but not
query it) + `is_stable=True` + a TM allowlist, ranked by **`total_magnetization` (maximize)**.
The notes self-critically flag that nearly every match is an oxide/ionic phase unfit as a hard
magnet. A clear instance of the flat-1.000 saturation in [#85](https://github.com/kianpu34593/materials-triage/issues/85).

```
Ranked shortlist (showing top 6 of 1238):
  1. Al2CoO4 (mp-aaabehde) — score 1.00
     Transition-metal oxide spinel with cobalt; thermodynamically stable on Materials Project.
     ⚠ Oxide spinels like Al2CoO4 typically exhibit low saturation magnetization and antiferromagnetic coupling, incompatible with permanent-magnet requirements; no published hard-magnet performance data.
  2. Ba(FeBr4)2 (mp-aaabgivk) — score 1.00
     Iron-containing ionic bromide; transition metal present but unusual bonding topology.
     ⚠ Ionic bromide complexes are not conventional permanent-magnet candidates; no published magnetic ordering temperature or saturation magnetization for this phase.
  3. Ba(FeO2)2 (mp-aaaabcis) — score 1.00
     Iron oxide ionic compound; thermodynamically stable but oxygen-rich stoichiometry.
     ⚠ Simple iron oxide in this composition range does not support the ferromagnetic hardness (coercivity, remanence) typical of permanent magnets; would require nanostructuring and exchange coupling.
  4. Ba2CoO4 (mp-aaaaayzn) — score 1.00
     Layered barium cobalt oxide; contains transition metal but lacks published hard-magnet data.
     ⚠ Layered ionic oxides exhibit low-dimensional magnetic correlations incompatible with permanent-magnet applications without severe nanostructuring or composite engineering.
  5. Ba2Fe2O5 (mp-aaacqbit) — score 1.00
     Iron-based oxy-compound offering ferrimagnetic platform analogous to hard ferrite candidates reviewed in rare-earth-free literature.
     ⚠ Ferrimagnetic oxides require nanostructuring and precise composition control to achieve practical coercivity; bulk phase properties not published.
  6. Ba2Mn(AsO4)2 (mp-aaabfukp) — score 1.00
     Manganese oxy-arsenate with transition metal; thermodynamically stable on Materials Project.
     ⚠ Complex oxy-anion compounds are not established permanent-magnet platforms; no saturation magnetization or Curie temperature data reported; magnetic structure unknown.

Caveats:
  ⚠ result set capped at 10000 candidates; ranking over a subset of the matching materials
```
</details>

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
