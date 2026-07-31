# Administrator console — implementation plan

Status: **proposal, for review.** No code has been written for this yet.

Audience: the existing UI is built for one persona — the buyer (采购/门店补货员) who reviews
suggestions, overrides quantities and reads attribution results. This document proposes a second
persona, the administrator, who operates and audits the system rather than using it to place orders.

---

## 1. What the backend already holds

The system captures considerably more than it shows. Three categories:

### 1.1 Captured, served by an API, never rendered

`AttributionRepository.get_case()` (`backend/attribution/repository.py:411-466`) already returns, for
every case:

- `attempts[]` — attempt number, state, error code/detail, `started_at`/`finished_at`,
  `duration_ms`, and per-attempt `model_calls` / `tool_calls` counts derived from trace events
- `trace_events[]` — the full redacted execution trace, with payloads
- `reports[]` — every report version, not just the latest
- `reviews[]` — reviewer subject, action, comment, which report version was reviewed

Endpoints whose data no page renders today. "Client" = whether a helper already exists in
`frontend/src/api.ts`; "Rendered" = whether any page or component actually calls it.

| Endpoint | Backing data | Client | Rendered |
|---|---|---|---|
| `GET /api/health` | `AttributionWorker.status()` — `running`, `healthy`, `last_poll_error` | ✗ | ✗ |
| `GET /api/attribution/jobs/{job_id}` | job → case rollup | ✓ | ✗ |
| `GET /api/attribution/traces/{trace_id}` | `attribution_trace_events` | ✓ | ✗ |
| `GET /api/attribution/cases/{id}/attempts/{n}/raw-log` | same, as NDJSON | ✓ | ✗ |
| `GET /api/attribution/knowledge` | `knowledge_entries` | ✓ | ✗ |
| `GET /api/attribution/diagnostic-agents` | harness registration | ✓ | ✗ |
| `GET /api/config` , `GET /api/config/schema` | global parameter config | partial | partial |
| `GET /api/trace/{tid}` , `GET /api/runs/{id}` | engine trace, full run payload | partial | partial |

Already wired and in use: `retryAttributionCase` and `cancelAttributionCase`, both surfaced to the
buyer in `AttributionCaseDetail.tsx` — one case at a time.

This is the important finding for sizing: **most of phase 1 is frontend wiring, not new backend
work.**

### 1.2 Captured, no read path at all

Written to the database, never exposed:

- `run_submission_audits` — every submission attempt and its status (`models.py:154-160`)
- `worker_leases` — which worker holds which case, and until when (`models.py:146-151`)
- `rejection_events` — the raw override events that seed attribution (`models.py:34-41`)
- `attribution_jobs` — listable only one-by-one via `get_job`

### 1.3 Not captured

- No history for parameter configuration. `backend/config.py` writes a single global
  `replenishment_config.json` under a threading lock; a change overwrites the previous value with
  no record of who changed what.
- No per-user activity log. See §2.

---

## 2. The blocking constraint: there is no identity model

`backend/api/main.py:156-158` defines exactly one credential pair. Token validation at
`main.py:185-204` asserts `claims["sub"] == AUTH_USERNAME`, so the system **cannot represent a
second principal**. The auth middleware (`main.py:211-220`) admits or rejects a request wholesale;
there is no per-route authorization anywhere in the codebase.

Two consequences:

1. An admin UI added purely in React would be decorative. A buyer reaches the same data with one
   `curl` and their own token. Authorization must exist in the backend or the feature is a
   pretence of control.
2. `human_reviews.reviewer_subject` and `replenishment_runs.submitted_by` already have columns for
   an actor, but today they always contain the literal string `dmall`. The audit trail records
   nothing distinguishing. Introducing a second account is what makes those columns meaningful.

### Decision taken: demo-grade second account

Keep the existing HMAC token scheme. Replace the single-pair check with a small in-process account
map and add a `role` claim.

```python
# main.py — replaces AUTH_USERNAME / AUTH_PASSWORD
ACCOUNTS = {
    "dmall":       {"password": ..., "role": "buyer"},
    "dmall-admin": {"password": ..., "role": "admin"},
}
```

- `_issue_token` embeds `{"sub", "role", "exp"}`.
- `_token_claims` validates `sub` against `ACCOUNTS` instead of a single constant, and returns the
  claims.
- New `require_admin` dependency raising 403; applied to every `/api/admin/*` route.
- New `GET /api/auth/me` returning `{"username", "role"}` so the UI renders from the server's
  answer rather than decoding the token itself.
- The authenticated subject is threaded into `reviewer_subject` and `submitted_by`.

This is deliberately proportionate to a demo/POC. It is **not** production authentication —
passwords stay in environment variables, there is no rotation, lockout, or session revocation, and
`CORSMiddleware` still allows all origins (`main.py:145`). Those are separate hardening items,
listed in §7.

---

## 3. Delivery shape

Same Vite SPA, but a visually distinct admin shell so an operator is never in doubt which mode they
are in.

- Routes under `/admin/*`, guarded by a `RequireAdmin` wrapper that reads `/api/auth/me`.
- A separate `AdminLayout` — distinct header treatment, denser tables, monospace for identifiers
  and timestamps.
- The buyer navigation (`App.tsx:232-238`) is untouched for buyers; admins additionally get an
  "管理控制台 / Admin" section.
- Non-admins hitting `/admin/*` are redirected to `/suggestions`; the backend independently returns
  403, so the guard is convenience, not the security boundary.
- Reuses the existing design tokens and `components/ui/*` primitives. No new component library.

---

## 4. Phase 1 — read-only console plus existing case operations

Scope chosen: observability and forensics, plus the retry/cancel/review operations that **already
exist** as endpoints, gathered into one operator surface. No new mutation capability.

### 4.1 System health (`/admin/health`)

Source: `GET /api/health` and new lightweight aggregates.

- Worker `running` / `healthy` / `last_poll_error` (`worker.py:81-87`)
- Queue depth by case state, and oldest queued case age
- Active leases: case, worker id, expiry, time remaining — surfaces stuck work
- Agent runtime availability (`GET /api/agent/status`)

New backend: `GET /api/admin/overview` returning the aggregates. Small.

### 4.2 Jobs and queue (`/admin/jobs`)

- Job list with per-job case-state rollup
- Drill into the cases of a job, filtered by state
- Bulk-select failed cases → retry, using the existing
  `POST /api/attribution/cases/{id}/retry`

New backend: `GET /api/admin/jobs` (list). Retry/cancel already exist.

### 4.3 Execution forensics (`/admin/cases/{case_id}`)

The admin counterpart to the buyer's `AttributionCaseDetail`. Same case, different questions —
the buyer asks "why did the order change", the admin asks "what did the agent do, how long did it
take, and why did it fail".

- Attempt timeline: state, duration, model/tool call counts, error code and detail
- Trace event viewer grouped by attempt, with expandable redacted payloads
- Report version history — every version, with a diff between consecutive versions
- Review history with reviewer identity
- Raw NDJSON log download per attempt

New backend: none. All of this is already in `get_case()` and the raw-log endpoint.

### 4.4 Knowledge and agents (`/admin/knowledge`)

- Published knowledge entries, their scope, expiry, and invalidation state
- Registered diagnostic sub-agents from `GET /api/attribution/diagnostic-agents`

New backend: none for read.

### 4.5 Clearing pending review tasks (`/admin/review-queue`)

Requested explicitly. It needs care, because "remove the pending review task" can mean three very
different things and two of them are dangerous.

#### What "pending" means today

The nav badge comes from `pending_review_count()` (`repository.py:681-687`), which counts cases in
`NEEDS_REVIEW`, `CHANGES_REQUESTED` or `FAILED`.

#### The trap: cancelling clears the badge but permanently blocks the run

`_readiness_in_session()` (`repository.py:861-901`) only counts a line as satisfied when a matching
case is `HUMAN_APPROVED` (line 877). `CANCELLED` is explicitly listed as a review-required blocker
(line 892). Meanwhile `pending_review_count` does **not** count `CANCELLED`.

So calling the existing `cancel_case` on a pending case:

- removes it from the badge — the queue *looks* clean, and
- leaves the run in `ATTRIBUTION_REVIEW_REQUIRED` forever, unsubmittable.

An admin doing the obvious thing would quietly strand the run. The recovery path exists
(`retry_case` accepts `FAILED` and `CANCELLED` back to `QUEUED`, `repository.py:614`) but nothing in
the UI would tell them they need it.

**Therefore the admin UI must never present a single unqualified "remove" button.** It must offer
two clearly distinct actions and state the consequence of each before it is taken.

#### Action A — Dismiss (cancel)

For cases that should not have existed, or are failing repeatedly and are not worth attributing.

- Backed by the existing `POST /api/attribution/cases/{id}/cancel`. No new state, no schema change.
- Clears the badge. **Does not** unblock the run.
- The confirmation must say so plainly, e.g. 「该行仍需人工批准后才能提交，撤销不会解除提交限制」.
- Reversible via retry, and the UI should offer that as the paired action.

#### Action B — Waive and approve

The action an operator actually wants when they say "remove this task": clear the queue *and* let
the run proceed.

This is a real business decision — the override goes downstream with no agent attribution behind
it — so it must be explicit, attributed and audited, never a bulk one-click.

- The legitimate mechanism already exists: the `MANUAL_AND_APPROVE` review action
  (`schemas.py`, `ReviewRequest`) writes a manual report plus an approval, reaching
  `HUMAN_APPROVED` and satisfying readiness.
- Admin surfacing should reuse it rather than inventing a new state, with a **mandatory** reason
  recorded in `HumanReview.notes` and the real admin subject in `reviewer_subject` (which only
  becomes meaningful after §2).
- Recommend flagging waived approvals in the report so they are distinguishable from
  agent-attributed ones — otherwise the audit trail cannot tell "explained" from "waived".

#### Action C — Hard delete: rejected

Deleting the case row is not proposed, and should be resisted if asked for:

- `execution_attempts`, `attribution_reports`, `human_reviews`, `attribution_trace_events` and
  `knowledge_entries` all carry a FK to `attribution_cases.case_id`. A delete either cascades and
  destroys the audit chain, or fails.
- The human-approval gate is the core compliance control of this system. An admin-only bypass that
  leaves no trace defeats the purpose of having built it.
- If the goal is a tidy queue, "archive" — cancel plus hidden from the default filter — achieves it
  without losing history.

#### Bulk handling

Both `cancel_case` and the review endpoint take `expected_case_version` for optimistic concurrency
(`repository.py:612`, `630`). A bulk action therefore has to send a per-case version and handle
partial failure. Proposed contract:

```
POST /api/admin/attribution/cases/bulk-dismiss
  { "cases": [{ "case_id": ..., "expected_case_version": ... }], "reason": "..." }
  -> { "succeeded": [...], "failed": [{ "case_id", "code", "message" }] }
```

Bulk **dismiss** is acceptable. Bulk **waive** is not — it would let one click push arbitrarily many
unattributed overrides downstream. Waive stays one case at a time, with a typed reason.

#### Queue page

- Filter by state, run, shop, SKU, age; sort by age so stuck work surfaces first.
- Show, per row, whether dismissing would strand a run — computed from submission readiness.
- Show which run each case blocks, with a link, so the consequence is visible before acting.

New backend: the bulk-dismiss endpoint, plus a readiness-impact field on the case list. Actions A
and B otherwise reuse existing repository methods.

### 4.6 Configuration inspector (`/admin/config`)

Read-only in this phase: the global parameter config and schema, plus effective resolution per
store/SKU. Editing is phase 3.

---

## 5. Phase 2 — evidence catalogue

This is the phase that changes attribution *quality* rather than visibility, and it addresses a
known defect.

`backend/attribution/seeds/{seasonality,holidays,substitutions}.json` are 94, 327 and 67 bytes and
can only be changed by redeploying. They currently restate the engine's own built-in factors from
`backend/skills/soft/factors.py`. Because the conservation anchor is the replayed baseline, evidence
that merely repeats an assumption the engine already applied explains exactly **zero** — which is
why attribution reports currently show `+0` and raise `EVIDENCE_MATCHES_BASELINE`.

Proposed:

- CRUD over evidence entries, persisted in a table rather than a file, with the seed JSON retained
  as the bootstrap default.
- Schema validation per evidence type on save.
- **A baseline-collision pre-check.** Before saving, replay the engine with and without the
  proposed evidence on a sample of recent cases and report how much it would explain. An admin sees
  *before committing* whether the entry can ever contribute, instead of discovering `+0` afterwards.
- Version and effective-date each entry so historical reports remain reproducible.

This phase carries real risk: evidence edits change attribution output for every subsequent case.
It needs the audit trail from §6 and should be gated behind a preview/apply flow.

---

## 6. Phase 3 — write operations (out of scope for the first delivery)

Listed for completeness, deliberately deferred:

- Parameter configuration editing with change history and who/when
- Submission-audit browser over `run_submission_audits`
- Run purge (`DELETE /api/runs`) with confirmation
- Worker pause / resume / drain
- Knowledge invalidation

Every item here mutates shared state, so each needs an audit record. Recommend introducing a single
`admin_audit_log` table when the first of these lands, rather than per-feature logging.

---

## 7. Known gaps this plan does not close

Recording these so they are decisions rather than oversights:

- `CORSMiddleware(allow_origins=["*"])` (`main.py:145`) — permissive for a demo, wrong for production.
- Passwords in environment variables, no hashing, rotation, lockout or revocation.
- Token cannot be revoked before `exp` (12h default).
- No rate limiting on `/api/auth/login`.
- `PUBLIC_PATHS` (`main.py:160`) exempts `/api/health`, which after §4.1 would reveal worker
  internals unauthenticated. **Health should be split**: a minimal public liveness probe, and the
  detailed operator view behind `require_admin`.

That last one is a real change this plan introduces and must not be forgotten.

---

## 8. Suggested sequence

| Step | Work | Depends on |
|---|---|---|
| 1 | Accounts map, `role` claim, `require_admin`, `/api/auth/me`, subject threading | — |
| 2 | Split `/api/health`; add `/api/admin/overview`, `/api/admin/jobs`, bulk-dismiss | 1 |
| 3 | `AdminLayout`, `RequireAdmin`, `/admin` routes, nav section | 1 |
| 4 | Health, jobs, forensics, review queue, knowledge, config-inspector pages | 2, 3 |
| 5 | Evidence catalogue: table, CRUD, validation, collision pre-check | 4 |

Steps 1–4 are the first deliverable. Step 5 is a separate discussion once the console exists.

### 8.1 Implementation status

Steps 1–4 are **implemented and verified**. Delivered:

| Area | Files |
|---|---|
| Accounts, roles, `require_api_auth` prefix guard, `/api/auth/me`, health split | `backend/api/main.py` |
| `admin_overview`, `list_jobs`, `review_queue`, `bulk_dismiss` | `backend/attribution/repository.py` |
| Authorisation and dismissal-semantics tests (10) | `backend/tests/test_admin_api.py` |
| Admin API client, types | `frontend/src/api.ts`, `frontend/src/types.ts` |
| Pages | `frontend/src/pages/Admin{Overview,Jobs,ReviewQueue,Knowledge}.tsx` |
| Nav section, `RequireAdmin`, admin-mode header | `frontend/src/App.tsx`, `frontend/src/styles.css` |
| Bilingual strings (question 1 answered: bilingual) | `frontend/src/i18n.tsx` |

Deviations from the plan as written, and why:

- **No separate `AdminLayout` component.** The existing shell already carries the nav, theme, language
  and logout controls; duplicating it would have forked the header. Instead the shell hides the
  buyer-only controls (engine toggle, shop/SKU selectors, master-data refresh) on `/admin/*`, adds an
  admin chip and an accent rule, and appends a separate nav section. Answers question 2: an admin can
  still use the buyer UI.
- **No dedicated forensics page (§4.3).** `AttributionCaseDetail.tsx` already renders attempts and
  trace events under its `trace` tab, so the review queue deep-links to `/attribution/:caseId` rather
  than duplicating that surface.
- **"Waive and approve" (§4.5 Action B) is not implemented** pending the answer to question 5. The
  review queue offers Dismiss only, and states in-product that approval must happen on the case detail
  page.
- `RequireAdmin` is a convenience guard only. Authorisation is enforced server-side for every path
  under `/api/admin/`, and the role is re-read from the accounts map on each request rather than
  trusted from the token.

---

## 9. Open questions for review

1. Should the admin console be bilingual like the buyer UI, or English-only? Full i18n roughly
   doubles the string work in phase 1.
2. Should an admin be able to see the *buyer* UI as well, or only the admin shell?
3. Is a third read-only "auditor" role wanted, or is buyer/admin sufficient?
4. Should trace payloads be visible verbatim to admins? They are already redacted for sensitive keys
   (`attribution/execution_trace.py:10-22`), but they include operator free text, which is untrusted
   input.
5. **Should "waive and approve" (§4.5 Action B) exist at all?** It is the only proposed capability
   that lets an override reach downstream without attribution. Excluding it keeps the approval gate
   absolute; including it is pragmatic when the agent cannot attribute a legitimate override — which,
   with the current seed evidence, is every case. Recommend including it, flagged and audited.
6. Should a dismissed (cancelled) case be hidden from the buyer's case list by default, or stay
   visible so the buyer understands why their run is still blocked? Recommend staying visible.
