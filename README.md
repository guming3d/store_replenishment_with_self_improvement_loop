# Agentic Store Replenishment & Attribution System

**Language:** **English (current)** · [中文](README.zh-CN.md)

An end-to-end store replenishment solution:

- A deterministic `(s, S)` replenishment engine produces auditable per-SKU order quantities.
- Microsoft Agent Framework orchestrates the replenishment agent.
- A harness attribution coordinator invokes constrained seasonality/holiday and substitution diagnostic capabilities.
- Deterministic counterfactual replay, Shapley allocation, and conservation checks perform the causal math.
- Daily sales feedback builds a decision outcome ledger that judges both the engine's recommendation and the human override against realized demand.
- Knowledge entries accumulate per store/SKU and earn weight only as measured accuracy proves them out.
- A React UI supports quantity adjustment, attribution review, and final submission.
- SQLite is used for local development; Azure PostgreSQL for multi-replica deployments.

## Core Business Constraint

Whenever a user sets `final_qty` to a value different from `chosen_qty`, an attribution matching the current quantity version must be produced **and** approved by a human before the entire run can be submitted.

```text
Generate replenishment recommendation
  -> Adjust quantity and provide a reason
  -> Attribution case created automatically
  -> Agent/harness diagnosis
  -> Deterministic counterfactual + Shapley attribution
  -> Human review
  -> All adjusted lines reach HUMAN_APPROVED
  -> Whole run submitted atomically and locked
```

There is no bypass that skips attribution or forces submission:

- Adjusting a quantity again marks the previous case `SUPERSEDED` and immediately invalidates its approval.
- A `partial` report cannot be approved directly; a human must supply the missing attribution first.
- After repeated agent failures a structured manual attribution is allowed, but it still requires review.
- Final submission is all-or-nothing; on success the run and its cases become read-only.
- Approval and knowledge capture are independent: approval only unlocks submission, while knowledge must be adjudicated candidate by candidate (accept / amend and accept / reject).

## Attribution Output: From "Allocated Quantity" to "Knowledge Candidate"

Allocations explain how *this* quantity difference splits across causes; they cannot answer "what should the system assume next time?"

The original allocation also had a structural flaw. The counterfactual used the engine's own recalculation as its baseline, and factors such as seasonality and holidays were *already applied* in that baseline — re-injecting them changed no quantity, so their allocation was structurally zero. Seasonality and holidays could never receive any share. The counterfactual now **turns off the specific causes the model named** and recalculates, producing `bare_baseline_qty`:

```text
Σ signed_contribution_qty + unexplained_signed_gap = override_qty − conservation_anchor_qty
```

`conservation_anchor_qty` equals `bare_baseline_qty` for counterfactual reports, and falls back to `recommended_qty` for human-written reports (a human-supplied reason is written directly against the difference anyway). Assumptions the model did not name keep their engine values: they are inputs to this decision, not objects of decomposition.

A residual is therefore normal and honest: it means the engine's assumptions explained their own share of the **recommended quantity**, while the store manager's disagreement remains unexplained by those assumptions — which is exactly what knowledge candidates (not allocations) are meant to answer.

So, beyond allocations, the report also emits `knowledge_candidates`: **the agent states only conditions and scope, never numbers**. Deterministic code then inverts the engine, searching for the value of the parameter named by `kind` that would reproduce the quantity the store manager actually ordered. A candidate is by definition "how much the engine's assumption must change," and degeneracy is structurally eliminated.

```text
calibration_status: EXACT | APPROXIMATE | UNREACHABLE | ALREADY_CORRECT | BLOCKED
acceptable = calibration_status ∈ {EXACT, APPROXIMATE}
```

- **`UNREACHABLE` is a useful conclusion, not a failure.** For example, a manager changes 48 to 10 while the full-case minimum order is 18 — no demand factor can reach 10, which proves the adjustment was not a demand judgment at all. In that case `proposed_value` is cleared, only `boundary_value` is kept, reviewers cannot accept a number the engine has already proven invalid, and the report is flagged with the `NO_CALIBRATABLE_CANDIDATE` risk.
- **`magnitude_plausible: false` warns but does not block.** Exact arithmetic may compute a 4.6× seasonal factor; whether that is believable is a human call, and a single `WRONG_MAGNITUDE` rejection is the loop doing its job.

### Review Records Rejections, Not Just Agreement

Review is upgraded from "approve / send back" to **per-candidate adjudication**, with rejection reasons drawn from a closed vocabulary (`WRONG_CAUSE` / `NOT_THE_DRIVER` / `WRONG_SCOPE` / `WRONG_MAGNITUDE` / `ONE_OFF_EVENT` / `INSUFFICIENT_EVIDENCE` / `ALREADY_KNOWN` / `OTHER`). Only a closed vocabulary makes statistics comparable across reviewers.

Rejections get their own table rather than a `REJECTED` status on knowledge entries: a rejection has no value and no parseable scope, and putting it in the table the engine reads would eventually cause misuse. Candidates are archived verbatim, so prompt changes can later be replayed against exactly the cases the model got wrong.

| Endpoint | Description |
|---|---|
| `GET /api/attribution/knowledge/rejections` | Rejected knowledge candidates, filterable by store/SKU/reason |
| `GET /api/attribution/knowledge/feedback` | Attribution agent scorecard: acceptance rate plus rejection stats grouped by reason |

## The Learning Loop: From Attribution to Accuracy

Attribution only explains the difference between the system's recommendation and the human override — it does not judge which one is closer to the store's real need. Relying on attribution and approval alone would make the system accumulate human *preference* rather than *correctness*. Daily sales feedback closes that gap:

```text
Submit and lock -> Open an evaluation window per decision (including unadjusted lines)
  -> Daily sales feedback (POS)
  -> Compute the hindsight-optimal quantity once the window closes
  -> Adjudicate ENGINE_BETTER / HUMAN_BETTER / TIE
  -> Update posterior confidence of the related knowledge entries
  -> Knowledge only starts influencing the engine once confidence is sufficient
```

Design points:

- **Unadjusted lines enter the ledger too.** Sampling only disagreements would mean the system never learns that "a recommendation a human accepted can also be wrong."
- **The evaluation window starts the day after the decision and spans lead time + coverage days**, and it reads only the frozen snapshot, never the current config — so past decisions are never judged with today's parameters.
- **Lost sales count as demand.** An empty shelf is unmet demand, not low demand; otherwise the smaller quantity would always look more correct.
- **An open window is `PENDING`, not a tie.** Only `COMPLETE` rows count toward the accuracy dashboard and knowledge promotion.
- **Case-pack differences are treated as ties.** A gap within half a case pack is outside either party's control and does not make one better.
- **Published knowledge starts at weight 0.** Weight comes from the Wilson lower bound of the hit rate, and `CANDIDATE -> SHADOW -> ACTIVE -> RETIRED` is driven entirely by measured outcomes; when the evidence turns, weight decays and the entry retires automatically without waiting for someone to notice.

### How Knowledge Actually Influences the Next Replenishment

The last leg of the loop: `ACTIVE` knowledge entries are resolved into engine inputs on every replenishment run. Before this, `engine.py` mentioned knowledge nowhere — entries could be accepted and promoted yet have zero effect on future recommendations.

```text
engine.KNOWLEDGE_TARGETS = factor_overrides.season | factor_overrides.holiday
                         | target_daily_demand_delta | params.fill_rate | params.shelf_max
```

- **A directive that cannot be applied must raise, not be ignored.** From the outside, silently skipped knowledge is indistinguishable from knowledge that had no effect. `SUBSTITUTION_RATE` points at a seed input rather than an engine parameter, so it is returned explicitly under `unsupported`.
- **Parameters passed explicitly by the caller always win.** Counterfactual recalculation pins a specific factor, and that probe must survive.
- **Every run emits a `knowledge.resolve` trace step** whether or not anything matched, so step numbering never drifts between runs.
- **The snapshot freezes `knowledge_applied`, and recalculation reads only the snapshot.** An entry accepted after the fact must not be allowed to rewrite the baseline it was attributed against, or every candidate's inversion would drift as the knowledge base grows.

New endpoints:

| Endpoint | Description |
|---|---|
| `POST /api/attribution/outcomes/daily-sales` | Feed back daily sales (optionally with lost sales); idempotent and triggers recomputation |
| `GET /api/attribution/outcomes` | Decision outcome ledger, filterable by store/SKU/status/verdict |
| `GET /api/attribution/accuracy` | Accuracy dashboard: engine vs. human MAE/MAPE, win rate, stockouts and overstock |
| `GET /api/attribution/knowledge` | Knowledge entries, filterable by store/SKU/status |
| `GET /api/attribution/knowledge/resolve` | The knowledge currently in effect for a given store × SKU, with weights |

## Main Modules

| Path | Description |
|---|---|
| `forecasting_cache/` | Precomputed store × SKU forecast inputs |
| `backend/engine.py` | Deterministic `(s, S)` replenishment engine |
| `backend/agent_runtime.py` | Original replenishment Agent Framework orchestration |
| `backend/attribution/` | Case/run state machine, harness, counterfactual attribution, worker, persistence |
| `backend/attribution/outcomes.py` | Outcome adjudication: windows, hindsight-optimal quantity, verdicts and accuracy aggregation (pure functions) |
| `backend/attribution/knowledge.py` | Knowledge confidence: Wilson lower bound, weights, state transitions, scope matching, and resolution into `engine_directives` (pure functions) |
| `backend/api/main.py` | FastAPI replenishment, attribution, review and submission API |
| `backend/migrations/` | SQLite/PostgreSQL Alembic migrations |
| `frontend/` | React + Ant Design replenishment and attribution review UI |
| `infra/` | PostgreSQL, managed identity, migration job and Container Apps |
| `CONTRACT.md` | Full API and state machine contract |

## Running Locally

### Prerequisites

- Python 3.11 or later
- Node.js 18 or later
- npm
- Optional: Azure CLI, for connecting to Microsoft Foundry

### 1. Install the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

The default is `sqlite+aiosqlite:///./attribution.db`. The required tables are created automatically on local startup.

To enable the real harness agent loop, edit `backend\.env`:

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
FOUNDRY_MODEL_DEPLOYMENT=<model-deployment-name>
ATTRIBUTION_WORKER_ENABLED=true
ATTRIBUTION_WORKER_CONCURRENCY=4
# Development only: capture each model/tool turn input and output in Agent Trace.
ATTRIBUTION_DEBUG_RAW_IO=true
```

Locally it uses `AzureCliCredential`:

```powershell
az login
```

Without Foundry configured, deterministic replenishment still works. The attribution worker records explicitly that the agent is unavailable and retries; once retries are exhausted the case enters `FAILED`, and the user must complete review via structured manual attribution — there is no way around it.

### 2. Install the frontend

```powershell
cd ..\frontend
npm install
```

### 3. Start backend and frontend

From the repository root:

```powershell
.\start-local.ps1
```

Or start them separately:

```powershell
# Terminal 1
cd backend
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

# Terminal 2
cd frontend
npm run dev
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000
- Default local username: `dmall`
- Default local password: `dmalltest`
- Default local admin username: `dmall-admin`
- Default local admin password: `dmalladmin`

Shared environments must configure credentials via `REPLENISH_DEMO_USERNAME`, `REPLENISH_DEMO_PASSWORD` and `REPLENISH_AUTH_SECRET` instead of the local defaults. The admin account is configured via `REPLENISH_ADMIN_USERNAME` and `REPLENISH_ADMIN_PASSWORD`; if either is empty, no admin account is registered and the admin console is unavailable.

### Admin Console

When signed in as an admin, an extra "Admin Console" group appears in the left navigation:

| Page | Purpose |
| --- | --- |
| Run overview | Attribution worker health, queue backlog, job status distribution, worker leases |
| Attribution batches | Attribution progress and status distribution per replenishment batch |
| Review queue | All pending / changes-requested / failed jobs, with bulk removal |
| Diagnostics & knowledge | Registered diagnostic agent declarations and active attribution knowledge entries |

All endpoints under `/api/admin/` are role-checked server side; a regular purchasing account receives 403.

**About "removing a pending review job":** removal is equivalent to cancelling the attribution job. It only clears the review queue — it does not mean the attribution passed. The replenishment submission gate accepts only "approved," so removing a job that was blocking submission leaves that replenishment run unsubmittable and requiring a new attribution. The review queue's "blocks submission" column and the confirmation dialog warn about this before you act; if the goal is to unblock, approve the job manually from its detail page instead.

### Generating Demo Attribution Data

Attribution can only quantify factors that actually exist in `backend/attribution/seeds/`. Picking an arbitrary store, product and date will usually land on the "no verifiable cause" branch — the conclusion is correct, but you see no allocation process at all. The script below picks combinations that match the seed data and generates four demo cases covering different attribution shapes in one go:

```powershell
cd backend
.venv\Scripts\python.exe scripts\seed_demo_attribution.py
```

| Scenario | Description |
| --- | --- |
| `multi` | Winter + New Year both produce quantities; evidence coverage ~94% with a small unexplained residual — the ideal attribution output |
| `partial` | The holiday yields a quantity; seasonality is deemed applicable but the month's factor is missing, so it is flagged `EVIDENCE_UNAVAILABLE_FOR_CAUSE` instead of being estimated |
| `single` | Only summer seasonality holds, and it is not diluted into several plausible-looking causes |
| `none` | The manager gave a reason but the data contains no support; the system says so instead of inventing a cause |

The script uses exactly the same endpoints as a real user (`/api/replenish/run` → `/api/replenish/adjust`), attribution is executed by the real agent, and each case takes about a minute. Add `--no-wait` to queue without waiting, or `--only multi` to generate a single scenario. Each run creates new cases, so repeated runs leave multiple records for the same store and product.

## UI Walkthrough

After signing in, start with the first item in the left navigation, "User Guide." It describes, in business terms, the full replenishment flow, the attribution path taken after a quantity change, the actions available in each state, and when to use "Replenishment Suggestions," "Attribution Jobs," "Replenishment Parameters" and "Run History."

### 1. Generate replenishment recommendations

1. Sign in and open "Replenishment Suggestions."
2. Choose a store and decision date. The system always assumes order today, arrive next day, on shelf the third day.
3. If needed, maintain current inventory and replenishment parameters first.
4. Choose the deterministic engine or the replenishment agent orchestration.
5. Click generate and review `chosen_qty`, inventory position, reorder point and the calculation explanation.

### 2. Adjust quantities and start attribution

1. Change the "final replenishment quantity" for one or more SKUs.
2. Click "Save draft and start attribution."
3. Select the required reason code and optionally add a description.
4. On save, the system creates a separate case for each adjusted SKU.

After a successful save you can:

- open the single case directly, or
- go to "Attribution Review" to see multiple cases under the same job.

### 3. Review the causal analysis

The case page polls automatically while in `QUEUED` and `RUNNING`. Once the report is generated, focus on:

- **Overview**: conclusion, primary causes, risks and conflicts
- **Evidence**: evidence sources, versions and freshness
- **Allocation**: signed contribution per cause, unexplained residual and the conservation formula
- **Trace**: worker attempts, tool executions and redacted events
- **Versions**: agent report and human review history

The attribution quantities always satisfy:

```text
sum of cause contributions + unexplained signed residual
  = override_qty - recommended_qty
```

### 4. Human review

| Action | When to use |
|---|---|
| `APPROVE` | The agent report is complete, not partial, and its evidence and allocation are acceptable |
| `REQUEST_CHANGES` | The report needs reprocessing or human supplementation |
| `AMEND_AND_APPROVE` | Approve after a human revises the cause contributions and summary |
| `MANUAL_AND_APPROVE` | Enter a structured manual attribution and approve after the agent has finally failed |

When revising cause contributions by hand, provide for each cause:

- cause code and domain
- signed contribution quantity
- explanation
- optional evidence references

Choosing "publish as knowledge" additionally requires a scope and an expiry. This option is not a prerequisite for the submission gate.

Below the cause table, the review drawer renders one card per **knowledge candidate**, showing the candidate's claim, recalculation effect, trigger conditions and scope, with a four-way adjudication:

| Verdict | Meaning |
|---|---|
| Defer | Nothing written, nothing recorded; the candidate stays in the report |
| Accept | Written to the knowledge base as-is (status `CANDIDATE`, weight 0) |
| Amend and accept | The reviewer rewrites the value, effective range and trigger conditions before writing |
| Reject | Not written to the knowledge base; written to the rejection ledger instead, with a mandatory reason |

Candidates with `acceptable: false` (for example, uncalibratable because of a full-case minimum) can only be rejected or deferred; the UI disables accept and amend so that no one accepts a value the engine has already proven invalid. Knowledge adjudication is likewise not a prerequisite for the submission gate, and no verdicts are accepted when sending back (`REQUEST_CHANGES`).

### 5. Submit the replenishment result

Return to "Replenishment Suggestions" and click "Submit final result":

- If any adjusted SKU lacks an up-to-date `HUMAN_APPROVED` case, the UI shows the blockers and links to the case.
- Once every adjustment is approved, the run enters `READY_TO_SUBMIT`.
- After a successful submission it becomes `SUBMITTED_LOCKED`, and neither quantities nor attribution cases can be modified.

## States

Run states:

```text
DRAFT
  -> ATTRIBUTION_RUNNING
  -> ATTRIBUTION_REVIEW_REQUIRED
  -> READY_TO_SUBMIT
  -> SUBMITTED_LOCKED
```

Case states:

```text
QUEUED -> RUNNING -> NEEDS_REVIEW -> HUMAN_APPROVED
                         |              ^
                         -> CHANGES_REQUESTED
RUNNING -> FAILED -> MANUAL_AND_APPROVE
any unsubmitted version -> SUPERSEDED
cancellable states -> CANCELLED
```

## Running the End-to-End Integration Test

The test below does not call the external Foundry service; it injects controlled diagnostic results that conform to the harness output schema. It still exercises the real API, SQLite persistence, the leased worker, deterministic counterfactual replay, Shapley allocation, human review, knowledge publication and final submission:

```powershell
cd backend
python -m pytest tests\test_attribution_api.py::test_replenishment_to_causal_analysis_human_review_and_submission -q
```

Run the full backend test suite:

```powershell
python -m pytest -q
```

Build the frontend:

```powershell
cd ..\frontend
npm run build -- --emptyOutDir
```

## Key Configuration

| Environment variable | Default / purpose |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint; agent capabilities are unavailable when empty |
| `FOUNDRY_MODEL_DEPLOYMENT` | Model deployment name used by the harness and replenishment agent |
| `ATTRIBUTION_DATABASE_URL` | SQLite by default locally; `postgresql+asyncpg` on Azure |
| `ATTRIBUTION_WORKER_ENABLED` | Whether to start the in-process attribution worker; default `true` |
| `ATTRIBUTION_WORKER_CONCURRENCY` | Concurrent cases per backend replica; default `4` |
| `ATTRIBUTION_DEBUG_RAW_IO` | Development debug switch; default `false`. When enabled, records each model/tool turn's input and output |
| `ATTRIBUTION_POSTGRES_ENTRA_AUTH` | Whether PostgreSQL uses an Entra token |
| `ATTRIBUTION_POSTGRES_MANAGED_IDENTITY_CLIENT_ID` | User-assigned managed identity for PostgreSQL |
| `FOUNDRY_MANAGED_IDENTITY_CLIENT_ID` | Optional user-assigned managed identity for Foundry; otherwise the system identity is used |
| `ATTRIBUTION_RUN_MIGRATIONS_ON_STARTUP` | Run safe Alembic migrations before an Azure revision starts |

The harness exposes only typed domain diagnostic tools. Shell, file read/write, web search, background agents, todo/mode, and tool auto-approval are all disabled; the model only judges whether evidence applies and is never responsible for any quantity or submission decision.

When the frontend submits a quantity change, it writes the current UI language into the attribution case: `zh-CN` for the Chinese UI, `en-US` for the English UI. The coordinator, diagnostic agents, summaries, cause explanations and deterministic cause labels all use that language, while machine fields (JSON keys, `cause_code`, `domain`, `evidence_refs`) stay stable. If a user retries a failed case under a different UI language, the retry uses the current UI language.

## Azure Deployment

A production deployment creates:

- Azure Container Apps for the frontend and backend
- Azure Database for PostgreSQL Flexible Server
- A PostgreSQL Entra administrator and a shared database managed identity
- A manual Alembic migration job
- Application Insights and Log Analytics
- Four attribution worker slots per backend replica, with two always-on replicas by default

The deployment script runs the migration job first and updates the application images only after it succeeds:

```powershell
cd infra
.\deploy.ps1
```

Linux/macOS:

```bash
cd infra
./deploy.sh
```

See [`infra/README.md`](infra/README.md) for detailed resource, identity and migration notes.

## Troubleshooting

### A case stays in `QUEUED`

Check that:

- `ATTRIBUTION_WORKER_ENABLED=true`
- the backend's `/api/health` reports `attribution_worker.running=true`
- the database is reachable
- worker replicas have not scaled to zero

### A case ends in `FAILED`

Inspect the case's attempts and trace:

- Is the Foundry endpoint or model deployment correct?
- Has `az login` been run locally?
- Does the Azure managed identity have permission to call Foundry?
- Did a model or tool call time out?

When the agent cannot be recovered, use `MANUAL_AND_APPROVE` to complete a structured manual attribution. The system never allows skipping attribution and submitting directly.

### How to confirm the attribution agent actually ran

Open the case's **Agent Trace** tab. Each attempt shows the real number of model calls and tool calls, and a redacted raw JSONL log can be downloaded. The log contains:

- `HARNESS_STARTED`
- `MODEL_CALL_STARTED`, `MODEL_CALL_COMPLETED` or `MODEL_CALL_FAILED`
- `TOOL_CALL_STARTED`, `TOOL_CALL_COMPLETED` or `TOOL_CALL_FAILED`
- `HARNESS_STRUCTURED_OUTPUT`
- `DETERMINISTIC_REPORT_COMPLETED`

The log records call boundaries, durations, model, token usage, tool names and structured result summaries. It does not record prompt bodies, tool argument values, credentials, or the model's private chain of thought. Historical attempts created before this feature shipped only have the original start/complete events; no model or tool call data is fabricated for them.

During development, to inspect each turn's raw input and output, set the following in `backend\.env`:

```dotenv
ATTRIBUTION_DEBUG_RAW_IO=true
```

After restarting the backend, new attempts additionally record `MODEL_RAW_INPUT`, `MODEL_RAW_OUTPUT`, `TOOL_RAW_INPUT` and `TOOL_RAW_OUTPUT`. These events may contain business data such as stores, products, inventory and forecasts, and should only be enabled briefly in a controlled development environment. Credential fields and the model's private reasoning content are always redacted.

### The submit button is still blocked after a quantity change

Check whether:

- a case is still `RUNNING`, `NEEDS_REVIEW` or `CHANGES_REQUESTED`
- the quantity was changed a second time, making the earlier case `SUPERSEDED`
- the report is `partial` and `AMEND_AND_APPROVE` has not been performed
- only some of the adjusted SKUs have been approved

### The frontend reports the backend is unavailable

Plain demo data can fall back to mocks in local development, but the attribution and submission endpoints never fake success. Verify the backend is running and the login token is valid, and check the API errors in the browser's Network panel.

See [`CONTRACT.md`](CONTRACT.md) for the complete field and error code reference.
