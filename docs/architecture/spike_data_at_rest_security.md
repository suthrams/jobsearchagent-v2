# Spike: Data-at-Rest Security for `data/v2.db`

> **Type:** Spike / options analysis (not an ADR). Produces a recommendation; the
> chosen option will be ratified in an ADR before implementation.
> **Date:** 2026-05-30 · **Status:** Open, awaiting decision.
> **Companion:** [`pii_data_flow.md`](pii_data_flow.md) findings **B1** (no
> encryption at rest), **B2** (no automatic retention/purge), **B3** (`raw_text`
> duplicated across columns). **Backing policy:**
> [ADR-040](adr/ADR-040-define-data-retention-and-privacy-policy.md).
> **Send-side already shipped:** [ADR-069](adr/ADR-069-redact-direct-identifiers-at-the-llm-seam.md).

---

## 1. Question this spike answers

The send-side PII gap (what we transmit to model providers) is closed (ADR-069).
What remains is the **at-rest** gap: every resume, parsed profile, workflow state,
and agent output sits in **plaintext** in `data/v2.db`, indefinitely. ADR-040
("store only as long as necessary; avoid indefinite raw resume storage; support
deletion") is accepted but was never implemented.

This spike maps what the current architecture allows, enumerates the options for
encryption and retention with honest trade-offs, and recommends a path.

---

## 2. What we are protecting, and from whom (threat model)

A precise threat model is the whole game here - it determines which options are
worth their cost.

### 2.1 The asset

`data/v2.db` (single SQLite file). Highest-value contents:

- `resumes.raw_text` - full resume verbatim (name, email, phone, address, history).
- `resumes.parsed_profile_json` - structured profile (includes `raw_text`).
- `workflow_runs.state_json` - serialized `WorkflowState`, which **still contains
  the full un-redacted profile** (load_resume writes `profile.model_dump()` into
  state; ADR-069 redacts only at the *egress to agents*, not in state). This is the
  single largest aggregate of PII per run.
- Agent-output JSON columns (reviews, advice, prep, tailoring, clinic) - quote
  resume content.

### 2.2 Threats, ranked for THIS deployment (single-user personal tool, Windows 11 Pro)

| # | Threat | Likelihood (personal) | Likelihood (if ever multi-user / hosted) | What defends it |
|---|--------|------------------------|------------------------------------------|-----------------|
| T1 | DB file copied out **without** the app/key: accidental `git add data/`, cloud-backup of `data/`, sync tool, sharing the file for debugging | Low-Med | Med-High | Encryption at rest (app-level or SQLCipher) |
| T2 | Device theft / disk imaging while powered off | Low (BitLocker likely on) | Med | OS full-disk encryption (BitLocker / FileVault / LUKS) |
| T3 | Full host compromise (attacker has app + filesystem + `.env`) | Low | Med | **Nothing app-side** - key sits with the data; needs HSM/KMS, out of scope |
| T4 | Unbounded accumulation: stale resumes/runs widen the blast radius of T1/T2 over time | Certain (grows daily) | Certain | Retention / purge |
| T5 | Plaintext PII in DB backups / snapshots | Med | High | Encryption + retention |

**Key honesty point (the same discipline the security article wants):** any key
stored in `.env` next to the DB does **not** defend T3. App-level encryption and
SQLCipher both defend the *same* thing - **T1/T5: the file leaving without the
key**. BitLocker defends **T2**. They are complementary, not substitutes. No
option here defends T3 without a managed key service.

---

## 3. What the current architecture allows (and limits)

Findings from the code (file:line in `pii_data_flow.md` and the read/write map):

### 3.1 Enablers

- **Single connection chokepoint:** every repository read/write funnels through
  `app/repositories/database.py::get_connection()`. One place to wrap.
- **A clean field-encryptable subset:** the columns the UI's direct reader never
  touches - `resumes.raw_text`, `resumes.parsed_profile_json`,
  `tailored_resumes.*`, `resume_clinic_reviews.*` - can be field-encrypted with
  **zero UI changes**.
- **Retention scaffolding exists:** `purge_old_data()` (`database.py:381`) already
  takes configurable windows and is unit-tested.

### 3.2 Hard limits (these rule options in/out)

- **L1 - The UI reader bypasses the chokepoint and uses `json_extract()`.**
  `app/ui/db_reader.py` opens its **own** `sqlite3.connect()` and runs SQLite
  `json_extract()` over `state_json`, `score_json`, `review_json`, `advice_json`,
  `prep_json`, `critic_output_json`, `audit_output_json` to pull nested fields for
  rendering. `json_extract` **requires plaintext JSON**. Therefore **app-level
  encryption of those specific columns breaks the UI** unless db_reader is changed
  to full-decrypt-then-extract in Python. SQLCipher does *not* have this problem
  (it decrypts beneath the SQL engine, so `json_extract` still works).

- **L2 - LangGraph `SqliteSaver` shares the SAME file.**
  `SqliteSaver.from_conn_string(".../data/v2.db")` (dependencies.py:567) writes its
  `checkpoints` table into `data/v2.db` using stock `sqlite3` with **no key
  parameter**. Whole-DB SQLCipher would require the checkpointer to open the file
  *with the key* - which the stock `SqliteSaver` cannot do. So SQLCipher forces one
  of: (a) move checkpoints to a separate unencrypted DB file, or (b) subclass /
  monkeypatch the saver's connection to inject `PRAGMA key`. Both are real work and
  ongoing maintenance risk against a third-party class.

- **L3 - Compiled dependency on Windows.** SQLCipher needs `sqlcipher3-binary` /
  `pysqlcipher3` with a compiled SQLCipher build. On the dev/primary platform
  (Windows 11) this is the historically painful install; the project today uses
  only the stdlib `sqlite3`.

- **L4 - `state_json` is both the biggest PII blob AND `json_extract`-ed by the
  UI.** So it is exactly the column we most want encrypted and the one
  field-encryption can least easily touch (collides with L1). Encrypting it
  app-side requires first relocating the handful of UI-needed run-metadata fields
  (roles, locations, thresholds, costs, counts) into dedicated plaintext columns on
  `workflow_runs`, then encrypting the remaining blob. This also overlaps with B3
  (we could stop storing the full profile in `state_json` at all).

- **L5 - Retention is unwired and incomplete.** `purge_old_data()` is **never
  called** anywhere in app code (no scheduler, endpoint, or startup hook), and it
  **skips** `resumes`, `tailored_resumes`, `resume_clinic_reviews` (the PII-heaviest
  tables) and does not cascade from `workflow_runs` to its child rows.

---

## 4. Options

### Option A - Retention + de-duplication only (no encryption)

Wire and complete `purge_old_data()`: extend it to the PII tables with cascade,
make windows configurable per ADR-040, expose a trigger (startup sweep +
manual endpoint/CLI), and reduce B3 (stop duplicating `raw_text` into
`state_json`; store a redacted profile or a `resume_id` reference there).

- **Defends:** T4, T5 (bounds how much PII exists), partially T1 (less to leak).
- **Cost/Risk:** Low. Pure Python, no new deps, no L2/L3 friction. Touches L4/B3
  carefully (resumption + db_reader run-metadata reads).
- **Residual:** Whatever is inside the window is still plaintext (T1/T2/T5 for
  recent data).

### Option B - App-level field encryption (+ retention)

Add `cryptography` (Fernet/AES-GCM). Encrypt the **subset the UI does not
`json_extract`**: `resumes.raw_text`, `resumes.parsed_profile_json`,
`tailored_resumes.*`, `resume_clinic_reviews.*`. Wrap encode/decode at the repo
serialization boundary. Key from `.env` (`DB_FIELD_KEY`), same pattern as the API
keys. Handle `state_json` per L4 (relocate UI metadata to plaintext columns, then
encrypt the rest) **or** defer `state_json` to Option A's dedup (store redacted
profile there so there is less to encrypt).

- **Defends:** T1, T5 for the encrypted columns; pairs with retention for T4.
- **Cost/Risk:** Medium. No compiled deps, **no L2/L3 problem** (we never touch the
  langgraph `checkpoints` table; the saver keeps using plaintext sqlite). The work
  is the codec + key management + the `state_json`/L4 surgery.
- **Residual:** Does not defend T2 (rely on BitLocker) or T3 (key on disk). The
  langgraph `checkpoints` table stays plaintext - it holds serialized state too, so
  this is a real gap unless we also scrub/limit what the checkpointer persists.

### Option C - SQLCipher whole-DB encryption (+ retention)

Replace `get_connection()` and `db_reader._connect()` with a SQLCipher connection
keyed from `.env`. Resolve L2 by moving checkpoints to a separate DB file (cleanest)
or keying the saver's connection.

- **Defends:** T1, T5 for the **entire** file, including the langgraph checkpoints
  table; `json_extract` keeps working (beats L1).
- **Cost/Risk:** High. L2 (saver) + L3 (Windows compiled dep) + a new failure mode
  (key mismatch bricks the whole DB) + every connection path must be updated.
- **Residual:** Same T2/T3 limits as B. Highest coverage, highest integration and
  operational risk.

### Option D - OS full-disk (BitLocker) + retention only; document the rest

Declare BitLocker the at-rest control for T2, implement Option A's retention to
bound T4/T5, and **explicitly document** that T1 (file leaks without key) is an
accepted residual for the personal-tool deployment, with Option B/C named as the
upgrade path if the tool is ever shared or hosted.

- **Defends:** T2 (BitLocker), T4/T5 (retention). Honest, low-cost.
- **Cost/Risk:** Lowest. Mostly Option A plus a documented decision.
- **Residual:** T1 unprotected by the app. Acceptable iff the file never leaves the
  encrypted volume without the owner's intent.

---

## 5. Comparison

| | A: Retention only | B: Field encryption | C: SQLCipher | D: BitLocker + retention |
|---|---|---|---|---|
| Defends T1 (file leak w/o key) | Partial | **Yes** (encrypted cols) | **Yes** (whole file) | No (app-side) |
| Defends T2 (device theft) | No | No (use FDE) | No (use FDE) | **Yes** (BitLocker) |
| Defends T4/T5 (accumulation/backups) | **Yes** | **Yes** | **Yes** | **Yes** |
| Covers langgraph checkpoints blob | n/a | **No** (gap) | **Yes** | No |
| Works with UI `json_extract` (L1) | n/a | Only on non-extracted cols | **Yes** | n/a |
| LangGraph saver friction (L2) | None | **None** | **High** | None |
| Windows compiled dep (L3) | None | None | **Yes** | None |
| New deps | None | `cryptography` | `sqlcipher3` | None |
| Effort | Low | Medium | High | Low |
| Reversibility / blast radius if misconfigured | Safe | Per-column | **Whole-DB brick risk** | Safe |

---

## 6. Recommendation

**Phase 1 now: Option A (retention + de-duplication), ratified in an ADR.**
It is the highest value per unit of risk: it directly implements the
already-accepted ADR-040, needs no new dependencies, has no L2/L3 friction, and
*shrinks the very surface every encryption option has to protect*. The B3 dedup
(stop storing the full profile in `state_json`) also defuses L4 in advance and
reduces the langgraph-checkpoint exposure that Option B cannot reach.

**Phase 2 (separate ADR, after Phase 1): Option B (app-level field encryption)
for the personal tool, with Option C named as the hosted/multi-user upgrade.**
Rationale: B defends the realistic threat (T1/T5 - the file leaking without the
key) at medium cost and, crucially, **without** the SQLCipher L2/L3 problems that
this codebase makes expensive. SQLCipher's one real advantage over B - covering
the langgraph `checkpoints` blob and surviving `json_extract` - is largely
neutralized if Phase 1's dedup keeps full PII out of `state_json` (and we can
additionally bound what the checkpointer retains). Reserve Option C for the day
the tool is shared or hosted, where whole-file transparency justifies the
integration cost.

**Explicitly: BitLocker (Option D's control) is assumed ON and is how we defend
device theft (T2). No app-side option defends full-host compromise (T3); that
needs a managed key service and is out of scope for a personal tool.**

### Why not each rejected primary

- **Not C first:** L2 (shared-file saver, stock sqlite, no key) + L3 (Windows build)
  + whole-DB brick risk are a lot of cost and operational fragility for a
  marginal gain over B once Phase 1 shrinks `state_json`.
- **Not B first (before retention):** encrypting a pile that grows forever is
  weaker than first bounding the pile; and doing dedup first removes the hardest
  part of B (the `state_json`/L4 surgery).
- **Not D alone:** leaves T1/T5 (the most plausible "oops, the file leaked" path)
  entirely to filesystem hygiene; fine as a documented interim, not as the end state.

---

## 7. Open questions to resolve in the Phase 1 ADR

1. **Retention windows** for the PII tables (`resumes`, `tailored_resumes`,
   `resume_clinic_reviews`) and **cascade** semantics from `workflow_runs`. Does
   deleting an old run delete its tailorings/reviews? (Probably yes; the resume row
   itself is longer-lived and user-owned.)
2. **What replaces the profile in `state_json`?** A redacted profile (ADR-069
   shape) or just `resume_id` + the fields the workflow nodes actually read back
   from state. Must not break resumption or db_reader's run-metadata extraction.
3. **Trigger for purge:** startup sweep, a manual `POST /admin/purge` endpoint, a
   CLI, or all three. (No scheduler infra exists today.)
4. **Re-use caveat:** a resume row is cache-keyed by `raw_text` hash and can back
   multiple runs - retention must not delete a resume still referenced by an
   in-window run.
5. **Phase 2 key management:** `.env` `DB_FIELD_KEY` vs OS keyring; key rotation
   story; behavior when the key is missing (fail closed vs read-only).

---

## 8. Decision log

- **2026-05-30:** Spike authored. Recommendation: Phase 1 = Option A (retention +
  dedup); Phase 2 = Option B (field encryption). Awaiting owner sign-off before
  writing the Phase 1 ADR.
