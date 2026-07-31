# API & Data Contract

Backend base: `http://localhost:8000`. All JSON. Frontend mocks against this.

## Data
- 228 shops × 10 SKUs × ~14mo daily; categories: standard, frozen(短保), beer, drinks, paper, home-clean, bath.
- Forecast cache: `forecasting_cache/forecast_{shop}_{sku}.json` → daily mean + p50/p90 demand.

## Endpoints
- `GET /api/health` → `{status}`
- `GET /api/skus` → `[{goods_code,goods_name,category}]`
- `GET /api/shops` → `[{shop_code,shop_name,city}]`
- `POST /api/replenish/run` body `{shop_code,goods_code,date,fill_rate?}` → single run result (shape below)
- `POST /api/replenish/batch` body `{shop_code,date,fill_rate?}` → `[run result...]` (every row carries the batch's shared `run_id`)
- `GET /api/exceptions` → list flagged items (high/low override)
- `GET /api/trace/{trace_id}` → orchestration steps (skill calls + deltas)

### Run result shape (continuous-review (s,S) model)
```
{shop, sku, sku_name, scenario,
 flow:"A", lead_time, apply_date, arrival_date, shelf_date,
 position,                       // inventory position IP at decision time
 reorder_point,                  // s = μ·L + z·σ·√L
 order_up_to,                    // S = μ·(L+C) + z·σ·√(L+C)
 triggered:bool, trigger:bool,   // IP ≤ s ?  (whether to order today)
 candidates:[{qty,method,risk}], chosen_qty, final_qty,   // final_qty is the current draft quantity
 safety_stock, target_stock, fill_rate, service_z, params,
 inventory:{on_hand,in_transit,reserved,expiring,recent_zero_days,available,phantom_suspect,source,overridden},
 demand, explanation, summary, exception:bool, engine, trace_id, steps, run_id}
```

### Store inventory (auto-fetched, staff-editable)
On selecting a store the Suggestions page auto-loads current inventory (deterministic
synthetic base with staff overrides). Staff can edit any field; edits persist to
`backend/inventory_overrides.json` and feed the next run.
- `GET /api/inventory?shop_code=&date=` → `{shop_code,date,rows:[{goods_code,goods_name,category,on_hand,in_transit,reserved,expiring,recent_zero_days,available,source,overridden}]}`
- `PUT /api/inventory` body `{shop_code,goods_code,fields:{on_hand?,in_transit?,reserved?,expiring?,...}}` → `{shop_code,goods_code,fields}` (persists the override; `overridden` lists edited keys)
- `DELETE /api/inventory/{shop_code}/{goods_code}` → `{shop_code,goods_code,removed:bool}` (revert to synthetic base)

### Mandatory attribution gate for quantity changes

Generated runs start in `DRAFT`. Unmodified recommendations can be submitted directly.
Saving any `final_qty != chosen_qty` is an explicit override and automatically creates
one attribution Case per changed SKU. Attribution is not an optional follow-up action.

- `POST /api/replenish/adjust`
  - body `{run_id,items:[{sku,final_qty,reason_code,reason_text?}]}`
  - returns `202` with `{run_id,changed,total_qty,results,job_id,case_ids,gate_status,run_version}`
  - a second edit supersedes the previous active Case and approval for that SKU.
- `GET /api/runs/{run_id}/submission-readiness`
  - returns `{run_id,run_version,status,ready,modified_count,approved_count,blockers}`.
- `POST /api/runs/{run_id}/submit` body `{expected_version}`
  - atomically verifies every changed SKU has the latest matching `HUMAN_APPROVED`
    attribution bound to `run_id + SKU + recommended_qty + override_qty + snapshot_hash`.
  - on success returns `{run_id,status:"SUBMITTED_LOCKED",submitted_at,submitted_by,run_version}`.
  - submission is all-or-nothing; success makes the run and quantities immutable.
  - P0 records the local locked submission and invokes a pluggable no-op downstream adapter.

Run states:

```text
DRAFT -> ATTRIBUTION_RUNNING -> ATTRIBUTION_REVIEW_REQUIRED
      -> READY_TO_SUBMIT -> SUBMITTED_LOCKED
```

`DELETE /api/runs` only clears unreferenced drafts. Submitted runs and any run referenced
by attribution, review, or knowledge records are retained for audit.

### Attribution Jobs and Cases

- `GET /api/attribution/jobs/{job_id}`
- `GET /api/attribution/cases?status=&shop_code=&goods_code=&direction=&job_id=&page=&page_size=`
- `GET /api/attribution/cases/{case_id}`
- `POST /api/attribution/cases/{case_id}/reviews`
  - body `{action,expected_version,comment?,causes?,summary?,knowledge_decisions?,publish_knowledge?,knowledge_scope?,knowledge_kind?,knowledge_category?,knowledge_prior_value?,knowledge_proposed_value?,knowledge_applies_from?,knowledge_applies_to?,knowledge_expires_at?}`
  - actions: `APPROVE`, `REQUEST_CHANGES`, `AMEND_AND_APPROVE`, `MANUAL_AND_APPROVE`.
  - `APPROVE` is invalid for partial reports; partial and failed cases require structured amendment/manual attribution.
  - `knowledge_decisions[]` is the per-candidate verdict, one entry per `candidate_id` the
    report produced: `{candidate_id,decision,cause_code?,kind?,domain?,scope_label?,scope_category?,applies_from?,applies_to?,prior_value?,proposed_value?,condition?,reject_reason?,note?,expires_at?}`.
    `decision` is `ACCEPT` | `AMEND` | `REJECT`. `REQUEST_CHANGES` cannot carry decisions —
    a report the reviewer is sending back has no verdict to record. Candidates the report
    marked `acceptable: false` cannot be accepted or amended, only rejected.
    Shop and goods scope are pinned from the case, so a reviewer may widen a candidate to a
    category but cannot bind it to a different store or SKU.
  - When `knowledge_prior_value`/`knowledge_proposed_value` are omitted the value is derived
    from the approved quantity gap spread across the horizon, so no entry is stored without a
    value the engine can replay.
- `POST /api/attribution/cases/{case_id}/retry`
- `POST /api/attribution/cases/{case_id}/cancel`
- `GET /api/attribution/review-count`
- `GET /api/attribution/diagnostic-agents`
- `GET /api/attribution/traces/{trace_id}`
- `GET /api/attribution/knowledge?shop_code=&goods_code=&status=&include_expired=`
- `GET /api/attribution/knowledge/resolve?shop_code=&goods_code=&category=&on_date=`
- `GET /api/attribution/knowledge/rejections?shop_code=&goods_code=&cause_code=&reason_code=&limit=`
- `GET /api/attribution/knowledge/feedback?date_from=&date_to=`
  - `{accepted_total,rejected_total,acceptance_rate,accepted_by_kind,rejected_by_cause}` — the
    diagnostic agents' report card. `rejected_by_cause` tallies the closed reason vocabulary so
    an agent's owner can see *which* way it is wrong, not merely that it was overruled.
- `GET /api/attribution/claims/feedback?date_from=&date_to=&shop_code=`
  - `{judged_total,by_verdict,by_reason_code,supported_rate,out_of_scope_total}` — the
    counterpart report card, for the claim the agents were asked to check (see below).

### The operator's stated reason

Every override carries a `reason_code` and optional `reason_text`. Both are untrusted: the
coordinator is shown them as `operator_claim` but is forbidden to treat them as proof, and no
allocation, Shapley value or knowledge value may be derived from them.

The verdict comparing that claim with the evidence is therefore **computed, never asked for**.
The model sees the claim, so a model asked to grade it would be grading its own anchor. Reports
carry `operator_claim` from deterministic code instead:

```text
verdict: SUPPORTED | UNCALIBRATED | CONTRADICTED | OUT_OF_SCOPE | UNVERIFIABLE
```

`SUPPORTED` requires both that a cause mapped from the reason code was found applicable *and*
that its knowledge candidate is `acceptable` — i.e. that the engine could be solved to the
quantity actually ordered. Fluent text cannot make a parameter reach a quantity it cannot reach,
so the verdict cannot be talked into existence. `UNCALIBRATED` is the applicable-but-unsolvable
case and, with `CONTRADICTED`, raises the `OPERATOR_CLAIM_UNSUPPORTED` risk flag.

`OUT_OF_SCOPE` is not a failing grade. `DEMAND_CHANGE` and `INVENTORY_CONSTRAINT` map to no
cause the registry can quantify, so such claims are counted but excluded from `supported_rate`:
the cause vocabulary fell short there, not the store. A large `out_of_scope_total` is a backlog
item for attribution — persistent `INVENTORY_CONSTRAINT` claims in particular point at stock
accuracy, not demand.

`unclaimed_supported_causes` lists causes the evidence backs that the store manager never
mentioned, so acting on a real signal one cannot name is never counted as a false claim.

The verdict is denormalised onto `attribution_reports.claim_verdict` so the summary groups in
SQL. It is null for manual reports, which are written without evidence, and those rows are
excluded from the rate rather than counted as agreement.

### Knowledge candidates

A report carries `knowledge_candidates[]` alongside `allocations[]`. An allocation says how a
past quantity split across causes; a candidate says what the engine should assume next time,
and is the only half of the report a reviewer can turn into knowledge.

Each candidate is produced by solving the engine backwards: deterministic code searches the
parameter named by `kind` for the value that reproduces the quantity the store manager actually
ordered. The agent supplies the condition and its scope; it supplies no numbers.

```text
calibration_status: EXACT | APPROXIMATE | UNREACHABLE | ALREADY_CORRECT | BLOCKED
acceptable = calibration_status in {EXACT, APPROXIMATE}
```

`UNREACHABLE` means no value of that parameter reaches the ordered quantity — typically an MOQ
or case-pack floor, i.e. the override was not a demand judgement at all. Such a candidate keeps
its `boundary_value` but has `proposed_value: null`, so a reviewer cannot accept a number the
engine has already shown cannot work, and the report gains the `NO_CALIBRATABLE_CANDIDATE` risk
flag. `magnitude_plausible: false` marks a value that is arithmetically exact but too far from
the engine's own prior to be credible on one case; it stays acceptable, because that judgement
belongs to the reviewer.

Rejections are stored in their own table rather than as a status on knowledge entries: a
rejection has no value and no scope to resolve, and must never be visible to the engine. The
candidate is kept verbatim on the rejection so a changed prompt can be replayed against the
cases it got wrong.

### Decision outcomes and accuracy

- `POST /api/attribution/outcomes/daily-sales`
  - body `{source?,records:[{shop_code,goods_code,sales_date,units_sold,lost_sales_units?,stockout?}]}`
  - Idempotent per `(shop_code, goods_code, sales_date)`; ingestion rescores every window the
    day falls into and returns `{ingested,outcomes_recomputed,outcomes_completed}`.
- `GET /api/attribution/outcomes?shop_code=&goods_code=&status=&verdict=&date_from=&date_to=`
- `GET /api/attribution/accuracy?shop_code=&goods_code=&date_from=&date_to=`

Outcome rows are opened at submission for every decided line, both overridden and accepted.
The judgement window runs from the decision date + 1 for lead time + coverage days, read from
the frozen snapshot rather than current configuration. Status is `PENDING` -> `PARTIAL` ->
`COMPLETE`; `verdict` stays `PENDING` until the window closes and only `COMPLETE` rows feed the
accuracy board or knowledge promotion.

```text
verdict: ENGINE_BETTER | HUMAN_BETTER | TIE   (TIE within half a case pack, min 1 unit)
knowledge: CANDIDATE -> SHADOW -> ACTIVE -> RETIRED   (RETIRED is terminal)
```

Knowledge is published inert (`effective_weight` 0) and earns weight from the Wilson lower
bound of its hit rate against completed outcomes; the narrowest matching scope wins.

### Knowledge reaching the engine

`engine.run(..., knowledge=[...])` takes the directives produced by
`attribution.knowledge.engine_directives()`. Each directive assigns an absolute value to one
input named in `engine.KNOWLEDGE_TARGETS`:

```text
factor_overrides.season | factor_overrides.holiday | target_daily_demand_delta
params.fill_rate        | params.shelf_max
```

Rules the loop depends on:

- A directive naming any other target raises. Knowledge that silently fails to apply is
  indistinguishable from knowledge that had no effect, so `SUBSTITUTION_RATE` — which names a
  seed input rather than an engine argument — is returned under `unsupported` instead.
- An input the caller passed explicitly always wins; knowledge only fills what was left open.
  Counterfactual replay pins factors, and that probe must survive.
- The engine returns `knowledge_applied[]` / `knowledge_skipped[]` and records a
  `knowledge.resolve` trace step on every run, applied or not, so step numbers never shift.
- `_recommendation_snapshot` freezes `knowledge_applied` and `replay_engine` replays *that*
  list rather than resolving live. An entry approved after a decision must not rewrite the
  baseline that decision is attributed against.
- Known gap: `_substitution_evidence` re-runs the engine for substitute SKUs **without**
  knowledge. It is synchronous and knowledge resolution is not; the substitute's own knowledge
  is a separate concern from the SKU being attributed, so the plumbing was left out rather
  than threaded through unused.

### Counterfactual anchor

Allocations are measured from `bare_baseline_qty`: the engine replayed with the causes the
model named switched off. Assumptions nobody questioned are left at the engine's own value —
they are inputs to the decision, not part of what is being decomposed.

```text
sum(signed_contribution_qty) + unexplained_signed_gap == override_qty - conservation_anchor_qty
```

`conservation_anchor_qty` equals `bare_baseline_qty` for counterfactual reports and defaults to
`recommended_qty` for manually written ones, whose reviewer-entered causes are stated straight
against the gap. The previous anchor was the engine's own reproduction of its advice, which
made every seasonal and holiday cause worth exactly zero: the coalition replay re-asserted the
factor the engine had already applied, so the difference was zero by construction.

A residual is therefore normal and honest. It says the engine's assumptions account for their
share of the *recommendation* while the store manager's disagreement remains unexplained by
them — which is what the knowledge candidate, not the allocation, exists to address.

Case states:

```text
QUEUED -> RUNNING -> NEEDS_REVIEW -> HUMAN_APPROVED
                           |       -> CHANGES_REQUESTED -> HUMAN_APPROVED
                           +------ -> FAILED -> manual attribution/review
                           +------ -> CANCELLED
Any non-submitted revision can become SUPERSEDED.
```

Only `HUMAN_APPROVED` unlocks the run. Fully explained automatic output is still reviewed.
Approval does not publish knowledge automatically; knowledge publication is an explicit
per-candidate verdict. Approval alone never grants a knowledge entry any engine weight —
that comes only from measured outcomes. Attribution endpoints never return frontend mock success.

## Replenishment model & parameter configuration
The system is a **fully-automatic continuous-review (s,S) reorder-point** replenishment
engine (不定时不定量): pick a store → the engine auto-fetches current inventory + demand
history and, per SKU, computes the inventory position `IP` and only orders when
`IP ≤ s`. The store replenishes from a **central warehouse** on one fixed schedule:
今天申请 → 明天到货 → 后天上架 (lead time `L = 2`). The Suggestions page does not
expose a flow selector.

Policy math (per SKU): `s = μ·L + z·σ·√L`, `S = μ·(L+C) + z·σ·√(L+C)`, where `μ`/`σ` are
reconstructed daily demand, `z` from the service level, `C` = coverage. If `IP ≤ s` the
order qty is `constraint_round(S − IP)` (case-pack / MOQ / shelf-cap rounding); otherwise
`0` (今日不补). Staff can override the final qty afterwards via `/api/replenish/adjust`.

Operational parameters are user-configurable and persisted to
`backend/replenishment_config.json`, split by **scope**:
- **store** (shared by every SKU in the store): `fill_rate` (服务水平), `coverage` (补货周期/覆盖天数 C)
- **sku** (per store+SKU): `case_pack`, `moq`, `shelf_max`

`lead_time` is **no longer a stored parameter** — it is fixed at 2 days by the
standard operational schedule.
`on_hand`/inventory is no longer a parameter either — it is auto-fetched (and staff-editable)
via the inventory endpoints. Resolution order per (store, sku): **store/SKU override →
store default → system default** (system defaults reproduce prior behaviour). The
Parameters page shows a store-level form (2 shared params) plus an editable per-SKU table
(3 SKU params) from `GET /api/config/store-skus`.

- `GET /api/config/schema` → `{params:[{key,type,scope,default,min,max,step,label,label_en,help,help_en}],defaults}`
  - params: `fill_rate`(percent), `coverage`(int) [store]; `case_pack`(int), `moq`(int), `shelf_max`(int) [sku]
  - `scope` splits the params: **store** = `fill_rate`,`coverage`; **sku** = `case_pack`,`moq`,`shelf_max`.
- `GET /api/config` → `{store:{[shop_code]:params}, sku:{[shop_code]:{[goods_code]:params}}}`
- `GET /api/config/status?shop_code=&goods_code=` → `{configured:bool, level:"sku"|"store"|"none", shop_code, goods_code}` (`goods_code` optional). The Suggestions page **no longer gates a run on config** — runs auto-execute against resolved (defaulted) params; the Parameters page is where params are tuned.
- `GET /api/config/effective?shop_code=&goods_code=` → `{shop_code,goods_code,effective,store,sku,sku_overrides}` (resolved params + the explicit store/SKU levels for editing)
- `PUT /api/config/store` body `{shop_code,params}` → saves the store-level defaults. **Only store-scoped keys are persisted** (`fill_rate`,`coverage`); other keys are ignored.
- `PUT /api/config/sku` body `{shop_code,goods_code,params}` → saves a store/SKU override. **Only sku-scoped keys are persisted** (`case_pack`,`moq`,`shelf_max`); an empty result clears it.
- `DELETE /api/config/store/{shop_code}` → removes a store default (falls back to system defaults)
- `DELETE /api/config/sku/{shop_code}/{goods_code}` → removes a store/SKU override (falls back to store default)
- `GET /api/config/store-skus?shop_code=` → `{shop_code, store, params:[ParamSpec], rows:[{goods_code,goods_name,category,level,effective,sku}]}` — the store's SKU assortment (from the forecast index) with each SKU's resolved params, explicit override, and config level. Drives the store+SKU parameter table editor.
- `PUT /api/config/sku/bulk` body `{shop_code, rows:[{goods_code,params}]}` → `{shop_code, saved:[{goods_code,params}], errors:[{goods_code,error}]}` — saves several store/SKU overrides in one request (drives "Save all"); unknown goods_codes are reported in `errors` without failing the batch.

Optional per-request override: `POST /api/replenish/run` and `/batch` accept an
optional `fill_rate` that overrides the resolved service level for that call only.
For compatibility, a request may include `flow: "A"`; any other value is rejected.

## Scenarios: standard|fresh|longtail|new|promo|holiday|season|stockout
## Skills: algo(deterministic): param-learn, safety-stock, target-stock, monte-carlo, rounding; soft(LLM-Δ): season, holiday, promo, new-product
