"""FastAPI backend for store replenishment. Serves the CONTRACT.md API.
Reads precomputed forecast cache; orchestrates skills via engine."""
from __future__ import annotations
import asyncio, base64, binascii, hashlib, hmac, json, os, sys, time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import run  # noqa
import config  # noqa: E402  # service-level (fill rate) configuration
from skills.data import inventory_feed  # noqa: E402  # current-inventory feed + overrides
from skills.data.inventory import inventory_position  # noqa: E402
from attribution.db import Database  # noqa: E402
from attribution.deterministic import (  # noqa: E402
    SeedRepository, snapshot_hash, substitute_codes_for_target,
)
from attribution.diagnostics import list_diagnostic_agents  # noqa: E402
from attribution.errors import AttributionError  # noqa: E402
from attribution import knowledge as knowledge_math  # noqa: E402
from attribution.repository import AttributionRepository  # noqa: E402
from attribution.schemas import (  # noqa: E402
    AdjustDraftRequest, CaseState, DraftOverrideEvent, KnowledgeDecisionInput, KnowledgeKind,
    KnowledgePublishRequest, KnowledgeRejectReason, OutcomeIngestRequest, ReviewRequest,
)
from attribution.worker import AttributionWorker  # noqa: E402

# Agent runtime is optional: import lazily/gracefully so the deterministic API
# keeps working even if agent-framework or Foundry creds are absent.
try:
    import agent_runtime  # noqa
    _AGENT_IMPORT_ERR = None
except Exception as _e:  # pragma: no cover - defensive
    agent_runtime = None  # type: ignore
    _AGENT_IMPORT_ERR = repr(_e)

CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "forecasting_cache")
def _load(n): return json.load(open(os.path.join(CACHE, n), encoding="utf-8"))
FC = _load("forecast_index.json"); SKUS = _load("skus.json"); SHOPS = _load("shops.json")
SKU_MAP = {s["goods_code"]: s for s in SKUS}
SHOP_MAP = {s["shop_code"]: s for s in SHOPS}
TRACES = {}

# ---- Run history (persisted so previous results survive restarts) ----
import uuid  # noqa: E402
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "run_history.json")
HISTORY_LIMIT = 50
RUNS: list[dict] = []

def _load_runs() -> None:
    global RUNS
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            RUNS = json.load(f)
        for r in RUNS:  # rebuild trace lookup so /api/trace keeps working after restart
            for res in r.get("results", []):
                if res.get("trace_id"):
                    TRACES[res["trace_id"]] = res
    except Exception:
        RUNS = []

def _save_runs() -> None:
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(RUNS[-HISTORY_LIMIT:], f, ensure_ascii=False)
    except Exception:
        pass

async def _record_run(engine: str, shop_code: str, results: list[dict], kind: str = "batch") -> dict:
    """Record a completed run in the database and return its API payload."""
    clean = [r for r in results if isinstance(r, dict) and not r.get("error")]
    run_id = uuid.uuid4().hex[:8]
    persisted_results = [{**result, "run_id": run_id} for result in clean]
    run_payload = {
        "run_id": run_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "engine": engine,
        "kind": kind,
        "shop_code": shop_code,
        "shop_name": (SHOP_MAP.get(shop_code) or {}).get("shop_name", shop_code),
        "count": len(persisted_results),
        "exception_count": sum(1 for r in persisted_results if r.get("exception")),
        "trigger_count": sum(1 for r in persisted_results if r.get("triggered")),
        "total_qty": sum(int(r.get("final_qty", 0) or 0) for r in persisted_results),
        "results": persisted_results,
    }
    await _repo().record_run(run_id, run_payload)
    return run_payload

def _run_summary(run: dict) -> dict:
    return {k: v for k, v in run.items() if k != "results"}

_load_runs()

attribution_database: Database | None = None
attribution_repository: AttributionRepository | None = None
attribution_worker: AttributionWorker | None = None


def _repo() -> AttributionRepository:
    if attribution_repository is None:
        raise HTTPException(status_code=503, detail="attribution database is not ready")
    return attribution_repository


async def _run_startup_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    alembic_config = Config(os.path.join(backend_root, "alembic.ini"))
    alembic_config.set_main_option(
        "script_location", os.path.join(backend_root, "migrations"))
    await asyncio.to_thread(command.upgrade, alembic_config, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global attribution_database, attribution_repository, attribution_worker
    if os.getenv("ATTRIBUTION_RUN_MIGRATIONS_ON_STARTUP", "").lower() == "true":
        await _run_startup_migrations()
    attribution_database = Database()
    if (attribution_database.settings.url.startswith("sqlite") or
            os.getenv("ATTRIBUTION_INIT_SCHEMA", "").lower() == "true"):
        await attribution_database.init_schema()
    attribution_repository = AttributionRepository(attribution_database.session_factory)
    if os.path.exists(HISTORY_FILE):
        await attribution_repository.import_legacy_run_history(HISTORY_FILE)
    if os.getenv("ATTRIBUTION_WORKER_ENABLED", "true").lower() == "true":
        attribution_worker = AttributionWorker(attribution_repository)
        await attribution_worker.start()
    try:
        yield
    finally:
        if attribution_worker:
            await attribution_worker.stop()
        await attribution_database.dispose()
        attribution_worker = None
        attribution_repository = None
        attribution_database = None


app = FastAPI(title="Store Replenishment Agentic Service", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(AttributionError)
async def attribution_error_handler(_request: Request, exc: AttributionError):
    return JSONResponse(
        {"detail": exc.message, "code": exc.code, "details": exc.details},
        status_code=exc.status_code,
    )

AZURE_MODE = os.getenv("AZURE_USE_MANAGED_IDENTITY", "").lower() == "true"
AUTH_USERNAME = os.getenv("REPLENISH_DEMO_USERNAME") or ("" if AZURE_MODE else "dmall")
AUTH_PASSWORD = os.getenv("REPLENISH_DEMO_PASSWORD") or ("" if AZURE_MODE else "dmalltest")
ADMIN_USERNAME = os.getenv("REPLENISH_ADMIN_USERNAME") or ("" if AZURE_MODE else "dmall-admin")
ADMIN_PASSWORD = os.getenv("REPLENISH_ADMIN_PASSWORD") or ("" if AZURE_MODE else "dmalladmin")
AUTH_SECRET = os.getenv("REPLENISH_AUTH_SECRET") or ("" if AZURE_MODE else "store-replenishment-demo-secret")
AUTH_TTL_SECONDS = int(os.getenv("REPLENISH_AUTH_TTL_SECONDS", str(12 * 60 * 60)))
PUBLIC_PATHS = {"/api/health", "/api/auth/login"}
ADMIN_PATH_PREFIX = "/api/admin/"

ROLE_BUYER = "buyer"
ROLE_ADMIN = "admin"


def _build_accounts() -> dict[str, dict[str, str]]:
    accounts: dict[str, dict[str, str]] = {}
    if AUTH_USERNAME and AUTH_PASSWORD:
        accounts[AUTH_USERNAME] = {"password": AUTH_PASSWORD, "role": ROLE_BUYER}
    # A collision would silently promote the buyer account to administrator, so the
    # administrator is dropped instead of overwriting an existing entry.
    if ADMIN_USERNAME and ADMIN_PASSWORD and ADMIN_USERNAME not in accounts:
        accounts[ADMIN_USERNAME] = {"password": ADMIN_PASSWORD, "role": ROLE_ADMIN}
    return accounts


ACCOUNTS = _build_accounts()


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(encoded: str) -> bytes:
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    return base64.urlsafe_b64decode(f"{encoded}{padding}")


def _token_signature(payload: str) -> str:
    digest = hmac.new(AUTH_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return _b64_encode(digest)


def _issue_token(username: str) -> str:
    payload = _b64_encode(json.dumps(
        {"sub": username, "role": ACCOUNTS[username]["role"],
         "exp": int(time.time()) + AUTH_TTL_SECONDS},
        separators=(",", ":"),
    ).encode("utf-8"))
    return f"{payload}.{_token_signature(payload)}"


def _token_claims(token: str) -> dict | None:
    if not _is_auth_configured():
        return None
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _token_signature(payload)):
        return None
    try:
        claims = json.loads(_b64_decode(payload).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        return None
    account = ACCOUNTS.get(claims.get("sub"))
    if account is None:
        return None
    if not isinstance(claims.get("exp"), int) or claims["exp"] < int(time.time()):
        return None
    # The role is re-read from the account rather than trusted from the token, so a
    # token minted before a role change cannot outlive that change.
    return claims | {"role": account["role"]}


def _is_auth_configured() -> bool:
    return bool(AUTH_SECRET and ACCOUNTS)


@app.middleware("http")
async def require_api_auth(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")
    claims = _token_claims(token.strip()) if scheme.lower() == "bearer" else None
    if claims is None:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    # Prefix matching rather than per-route dependencies, so an administrator route
    # added later cannot be left unguarded by omission.
    if request.url.path.startswith(ADMIN_PATH_PREFIX) and claims["role"] != ROLE_ADMIN:
        return JSONResponse({"detail": "Administrator role required"}, status_code=403)
    request.state.subject = claims["sub"]
    request.state.role = claims["role"]
    return await call_next(request)

class RunReq(BaseModel):
    shop_code: str; goods_code: str; date: str | None = None
    flow: Literal["A"] = "A"
    fill_rate: float | None = Field(default=None, ge=0.5, le=0.9999)
class BatchReq(BaseModel):
    shop_code: str; date: str | None = None
    flow: Literal["A"] = "A"
    fill_rate: float | None = Field(default=None, ge=0.5, le=0.9999)

class LoginReq(BaseModel):
    username: str
    password: str

class InventoryReq(BaseModel):
    shop_code: str
    goods_code: str
    fields: dict
class AdjustItem(BaseModel):
    sku: str
    final_qty: float
    reason_code: str = Field(min_length=1, max_length=128)
    reason_text: str | None = Field(default=None, max_length=4000)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)
class AdjustReq(BaseModel):
    run_id: str
    items: list[AdjustItem]
    expected_version: int | None = Field(default=None, ge=1)
    output_language: Literal["zh-CN", "en-US"] = "zh-CN"


class VersionReq(BaseModel):
    expected_version: int = Field(ge=1)


class RetryReq(VersionReq):
    output_language: Literal["zh-CN", "en-US"] = "zh-CN"


class ReviewCauseReq(BaseModel):
    cause_code: str = Field(min_length=1, max_length=128)
    domain: str | None = Field(default=None, max_length=128)
    signed_contribution_qty: float
    explanation: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(default_factory=list)


class KnowledgeDecisionReq(BaseModel):
    """The reviewer's verdict on one candidate the agent proposed."""
    candidate_id: str = Field(min_length=1, max_length=128)
    decision: Literal["ACCEPT", "AMEND", "REJECT"]
    cause_code: str | None = Field(default=None, max_length=128)
    kind: KnowledgeKind | None = None
    domain: str | None = Field(default=None, max_length=128)
    scope_label: str | None = Field(default=None, max_length=32)
    scope_category: str | None = Field(default=None, max_length=128)
    applies_from: date | None = None
    applies_to: date | None = None
    prior_value: float | None = None
    proposed_value: float | None = None
    condition: str | None = Field(default=None, max_length=500)
    reject_reason: KnowledgeRejectReason | None = None
    note: str | None = Field(default=None, max_length=2000)
    expires_at: datetime | None = None


class AttributionReviewReq(BaseModel):
    action: Literal["APPROVE", "REQUEST_CHANGES", "AMEND_AND_APPROVE", "MANUAL_AND_APPROVE"]
    expected_version: int = Field(ge=1)
    expected_report_version: int | None = Field(default=None, ge=1)
    comment: str | None = Field(default=None, max_length=4000)
    causes: list[ReviewCauseReq] | None = None
    summary: str | None = Field(default=None, max_length=4000)
    knowledge_decisions: list[KnowledgeDecisionReq] | None = Field(default=None, max_length=20)
    publish_knowledge: bool = False
    knowledge_scope: str | None = None
    knowledge_kind: KnowledgeKind = KnowledgeKind.DEMAND_LEVEL
    knowledge_category: str | None = None
    knowledge_prior_value: float | None = None
    knowledge_proposed_value: float | None = None
    knowledge_applies_from: date | None = None
    knowledge_applies_to: date | None = None
    knowledge_expires_at: datetime | None = None


class BulkDismissItem(BaseModel):
    case_id: str = Field(min_length=1, max_length=36)
    expected_version: int = Field(ge=1)


class BulkDismissReq(BaseModel):
    # Bulk dismissal is bounded: it clears the review queue without ever satisfying
    # submission readiness, so a large accidental batch is expensive to undo.
    cases: list[BulkDismissItem] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)


class NoopSubmissionAdapter:
    async def submit(self, run_id: str, accepted_overrides: dict[str, int]) -> dict:
        return {"adapter": "noop-v1", "run_id": run_id, "accepted_count": len(accepted_overrides)}

class StoreConfigReq(BaseModel):
    shop_code: str
    params: dict
class SkuConfigReq(BaseModel):
    shop_code: str
    goods_code: str
    params: dict
class SkuBulkItem(BaseModel):
    goods_code: str
    params: dict
class SkuBulkReq(BaseModel):
    shop_code: str
    rows: list[SkuBulkItem]

def _dt(s):
    if not s: return datetime(2025, 6, 1)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try: return datetime.strptime(s, fmt)
        except ValueError: continue
    return datetime(2025, 6, 1)

def _worker_status() -> dict:
    return attribution_worker.status() if attribution_worker else {
        "running": False, "healthy": True, "last_poll_error": None,
        "crash_count": 0, "crash_reason": None,
    }


@app.get("/api/health")
def health():
    # Unauthenticated liveness probe. Operational detail lives behind
    # /api/admin/overview because this path is exempt from authentication.
    if not _worker_status()["healthy"]:
        raise HTTPException(status_code=503, detail={"status": "degraded"})
    return {"status": "ok", "pairs": len(FC)}
@app.post("/api/auth/login")
def login(req: LoginReq):
    if not _is_auth_configured():
        raise HTTPException(status_code=503, detail="authentication is not configured")
    username = req.username.strip()
    account = ACCOUNTS.get(username)
    if account is None or not hmac.compare_digest(req.password, account["password"]):
        raise HTTPException(status_code=401, detail="invalid username or password")
    return {"access_token": _issue_token(username), "token_type": "bearer",
            "expires_in": AUTH_TTL_SECONDS, "role": account["role"], "username": username}
@app.get("/api/auth/me")
def auth_me(request: Request):
    return {"username": request.state.subject, "role": request.state.role}
@app.get("/api/skus")
def skus(): return SKUS
@app.get("/api/shops")
def shops(): return SHOPS

async def _live_knowledge(shop_code: str, goods_code: str, on_date: str | None) -> list[dict]:
    """Resolve the knowledge currently active for one store and SKU into engine inputs.

    This is the return leg of the review loop: an entry a reviewer accepted and
    that outcomes then promoted to ACTIVE only earns its keep by changing the
    next recommendation, and until this call existed nothing read the knowledge
    table at decision time at all.
    """
    sku_info = SKU_MAP.get(goods_code) or {}
    entries = await _repo().active_knowledge_for(
        shop_code, goods_code, category=sku_info.get("category"),
        on_date=str(on_date) if on_date else None)
    return knowledge_math.engine_directives(entries)["directives"]


@app.post("/api/replenish/run")
async def run_one(r: RunReq):
    fc = FC.get(f"{r.shop_code}_{r.goods_code}")
    if not fc: return {"error": "no forecast for shop/sku"}
    knowledge = await _live_knowledge(r.shop_code, r.goods_code, r.date)
    res = run(r.shop_code, SKU_MAP[r.goods_code], fc, _dt(r.date), flow=r.flow, fill_rate=r.fill_rate, knowledge=knowledge); TRACES[res["trace_id"]] = res
    rec = await _record_run("algo", r.shop_code, [res], kind="single")
    return rec["results"][0]

@app.post("/api/replenish/batch")
async def run_batch(r: BatchReq):
    out = []
    for k, fc in FC.items():
        s, sku = k.split("_", 1)
        if s == r.shop_code:
            knowledge = await _live_knowledge(s, sku, r.date)
            res = run(s, SKU_MAP[sku], fc, _dt(r.date), flow=r.flow, fill_rate=r.fill_rate, knowledge=knowledge); TRACES[res["trace_id"]] = res; out.append(res)
    if out:
        rec = await _record_run("algo", r.shop_code, out, kind="batch")
        return rec["results"]
    return []

@app.get("/api/exceptions")
async def exceptions():
    runs = await _repo().list_run_views(limit=50)
    return [
        result for item in runs for result in item.get("results", [])
        if result.get("exception")
    ][:50]

@app.get("/api/trace/{tid}")
async def trace(tid: str):
    return await _repo().find_replenishment_trace(tid)

# ---- Run history ----
@app.get("/api/runs")
async def list_runs():
    return [_run_summary(run) for run in await _repo().list_run_views(limit=HISTORY_LIMIT)]

@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    return await _repo().get_run_view(run_id)

@app.delete("/api/runs")
async def clear_runs():
    return {"cleared": True, "deleted": await _repo().clear_unreferenced_drafts()}

# ---- Replenishment parameter configuration (store-level + store/SKU-level) ----
@app.get("/api/config/schema")
def config_schema():
    """Parameter catalogue (labels/min/max/step/default) driving the config panel."""
    return {"params": config.param_specs(), "defaults": config.DEFAULTS}

@app.get("/api/config")
def config_all():
    """Every stored store default and store/SKU override."""
    return config.get_all()

@app.get("/api/config/status")
def config_status(shop_code: str, goods_code: str | None = None):
    """Whether an explicit config exists for the requested scope (drives step 2)."""
    return config.is_configured(shop_code, goods_code)

@app.get("/api/config/effective")
def config_effective(shop_code: str, goods_code: str | None = None):
    """Resolved parameters plus the explicit store/SKU levels for editing."""
    return {
        "shop_code": shop_code,
        "goods_code": goods_code,
        "effective": config.resolve(shop_code, goods_code),
        "store": config.get_store_config(shop_code),
        "sku": config.get_sku_config(shop_code, goods_code) if goods_code else None,
        "sku_overrides": config.list_sku_configs(shop_code),
    }

@app.put("/api/config/store")
def put_store_config(req: StoreConfigReq):
    """Save the store-level default parameters."""
    try:
        return {"shop_code": req.shop_code, "params": config.set_store_config(req.shop_code, req.params)}
    except ValueError as e:
        return {"error": str(e)}

@app.put("/api/config/sku")
def put_sku_config(req: SkuConfigReq):
    """Save a store/SKU-level override (only the supplied parameters)."""
    if req.goods_code not in SKU_MAP:
        return {"error": f"unknown goods_code {req.goods_code}"}
    try:
        return {"shop_code": req.shop_code, "goods_code": req.goods_code,
                "params": config.set_sku_config(req.shop_code, req.goods_code, req.params)}
    except ValueError as e:
        return {"error": str(e)}

@app.delete("/api/config/store/{shop_code}")
def delete_store_config(shop_code: str):
    """Remove a store-level default so it falls back to the system defaults."""
    return {"shop_code": shop_code, "removed": config.clear_store_config(shop_code)}

@app.delete("/api/config/sku/{shop_code}/{goods_code}")
def delete_sku_config(shop_code: str, goods_code: str):
    """Remove a store/SKU override so it falls back to the store default."""
    return {"shop_code": shop_code, "goods_code": goods_code,
            "removed": config.clear_sku_config(shop_code, goods_code)}

def _store_goods(shop_code: str) -> list[str]:
    """goods_codes that have a forecast for this store (the store's assortment)."""
    return [k.split("_", 1)[1] for k in FC if k.split("_", 1)[0] == shop_code]

@app.get("/api/config/store-skus")
def config_store_skus(shop_code: str):
    """The store's SKU assortment with each SKU's resolved + explicit parameters.

    Drives the store+SKU parameter editor: one row per SKU in the store, each
    carrying its effective values, any explicit override, and the config level.
    """
    store = config.get_store_config(shop_code)
    overrides = config.list_sku_configs(shop_code)
    rows = []
    for gc in _store_goods(shop_code):
        sku = SKU_MAP.get(gc, {})
        rows.append({
            "goods_code": gc,
            "goods_name": sku.get("goods_name", gc),
            "category": sku.get("category", ""),
            "level": config.is_configured(shop_code, gc)["level"],
            "effective": config.resolve(shop_code, gc),
            "sku": overrides.get(gc),
        })
    rows.sort(key=lambda r: r["goods_name"])
    return {"shop_code": shop_code, "store": store, "params": config.param_specs(), "rows": rows}

@app.put("/api/config/sku/bulk")
def put_sku_bulk(req: SkuBulkReq):
    """Save several store/SKU overrides in one request (drives 'Save all')."""
    saved, errors = [], []
    for item in req.rows:
        if item.goods_code not in SKU_MAP:
            errors.append({"goods_code": item.goods_code, "error": f"unknown goods_code {item.goods_code}"})
            continue
        try:
            params = config.set_sku_config(req.shop_code, item.goods_code, item.params)
            saved.append({"goods_code": item.goods_code, "params": params})
        except ValueError as e:
            errors.append({"goods_code": item.goods_code, "error": str(e)})
    return {"shop_code": req.shop_code, "saved": saved, "errors": errors}

# ---- Current inventory feed (门店当前库存, editable by staff) ----
@app.get("/api/inventory")
def list_inventory(shop_code: str, date: str | None = None):
    """The store's current inventory (per SKU), synthetic base with staff overrides.

    Drives the auto-loaded, editable inventory table on the suggestions page.
    """
    d = _dt(date).strftime("%Y-%m-%d")
    rows = []
    for gc in _store_goods(shop_code):
        sku = SKU_MAP.get(gc, {})
        fc = FC.get(f"{shop_code}_{gc}", {})
        mean = fc.get("mean", 0)
        inv = inventory_feed.get_inventory(shop_code, gc, mean, d)
        pos = inventory_position(inv["on_hand"], inv["in_transit"],
                                 inv["reserved"], inv["expiring"],
                                 days_to_expiry=inv.get("days_to_expiry"),
                                 daily_demand=mean)
        rows.append({
            "goods_code": gc, "goods_name": sku.get("goods_name", gc),
            "category": sku.get("category", ""),
            "on_hand": inv["on_hand"], "in_transit": inv["in_transit"],
            "reserved": inv["reserved"], "expiring": inv["expiring"],
            "days_to_expiry": inv.get("days_to_expiry", 0),
            "recent_zero_days": inv["recent_zero_days"], "available": pos["available"],
            "daily_mean": round(float(mean or 0.0), 2),
            "source": inv["source"], "overridden": inv["overridden"],
        })
    rows.sort(key=lambda r: r["goods_name"])
    return {"shop_code": shop_code, "date": d, "rows": rows}

@app.put("/api/inventory")
def put_inventory(req: InventoryReq):
    """Persist a staff override of a SKU's current inventory."""
    if req.goods_code not in SKU_MAP:
        return {"error": f"unknown goods_code {req.goods_code}"}
    saved = inventory_feed.set_inventory(req.shop_code, req.goods_code, req.fields)
    return {"shop_code": req.shop_code, "goods_code": req.goods_code, "fields": saved}

@app.delete("/api/inventory/{shop_code}/{goods_code}")
def delete_inventory(shop_code: str, goods_code: str):
    """Drop a SKU inventory override so it reverts to the synthetic base."""
    return {"shop_code": shop_code, "goods_code": goods_code,
            "removed": inventory_feed.clear_inventory(shop_code, goods_code)}

# ---- Staff adjustment of the recommended order quantity ----
def _substitution_evidence(shop_code: str, goods_code: str, result: dict) -> dict:
    """Freeze each substitute's demand and stock position alongside the target's.

    The substitution quantifier needs the *substitute's* numbers, which live in a different
    SKU's engine run. Capturing them here means attribution replays the position that held
    when the buyer decided, not whatever the synthetic feed returns days later.
    """
    seed = SeedRepository().load("substitutions")
    substitutes = substitute_codes_for_target(goods_code, seed)
    if not substitutes:
        return {}
    decision_date = result.get("apply_date")
    target_demand = (result.get("demand") or {}).get("true_mean")
    if not decision_date or target_demand is None:
        return {}
    flow = result.get("flow", "A")
    evidence: dict = {}
    for substitute in substitutes:
        forecast = FC.get(f"{shop_code}_{substitute}")
        sku_info = SKU_MAP.get(substitute)
        if not forecast or not sku_info:
            continue  # a relationship may name a SKU this store does not carry
        try:
            replay = run(shop_code, sku_info, forecast,
                         datetime.fromisoformat(str(decision_date)).date(), flow=flow)
        except Exception:  # pragma: no cover - a substitute must never break the override
            continue
        evidence[substitute] = {
            "substitute_goods_code": substitute,
            "substitute_goods_name": sku_info.get("goods_name"),
            "substitute_reconstructed_daily_demand": (replay.get("demand") or {}).get(
                "true_mean", 0),
            "substitute_reorder_point": replay.get("reorder_point", 0),
            "substitute_available_position": replay.get("position", 0),
            "target_true_daily_demand": target_demand,
        }
    return evidence


def _recommendation_snapshot(run_payload: dict, result: dict) -> dict:
    shop_code = str(result.get("shop") or run_payload.get("shop_code"))
    goods_code = str(result.get("sku"))
    forecast = FC.get(f"{shop_code}_{goods_code}")
    sku_info = SKU_MAP.get(goods_code)
    if not forecast or not sku_info:
        raise HTTPException(
            status_code=422,
            detail=f"immutable replay inputs are unavailable for {shop_code}/{goods_code}",
        )
    return {
        "shop": shop_code,
        "shop_name": (SHOP_MAP.get(shop_code) or {}).get("shop_name", shop_code),
        "sku_info": sku_info,
        "forecast": forecast,
        "decision_date": result.get("apply_date"),
        "inventory_snapshot": result.get("inventory"),
        "flow": result.get("flow", "A"),
        "on_promo": forecast.get("promo_uplift", 1) > 1.2,
        "params": result.get("params"),
        "fill_rate": result.get("fill_rate"),
        # Frozen so the replay reproduces the decision that was actually taken.
        # Resolving knowledge live at replay time would let an entry approved
        # after the fact rewrite the past baseline, and every counterfactual and
        # candidate calibration anchored on it would drift with it.
        "knowledge_applied": result.get("knowledge_applied") or [],
        "substitution_evidence": _substitution_evidence(shop_code, goods_code, result),
    }


@app.post("/api/replenish/adjust", status_code=202)
async def adjust_run(req: AdjustReq):
    """Persist immutable override events and enqueue one Case per changed SKU."""
    stored = await _repo().get_run(req.run_id, include_payload=True)
    payload = stored["payload"]
    results_by_sku = {
        str(result.get("sku")): result for result in payload.get("results", [])
    }
    now = datetime.now(timezone.utc)
    events = []
    for item in req.items:
        result = results_by_sku.get(item.sku)
        if not result:
            raise HTTPException(status_code=422, detail=f"run has no SKU {item.sku}")
        snapshot = _recommendation_snapshot(payload, result)
        events.append(DraftOverrideEvent(
            event_id=item.event_id or str(uuid.uuid4()),
            source_run_id=req.run_id,
            source_trace_id=str(result.get("trace_id") or ""),
            shop_code=str(result.get("shop") or payload.get("shop_code")),
            goods_code=item.sku,
            decision_date=str(result.get("apply_date")),
            recommended_qty=max(0, int(result.get("chosen_qty", 0))),
            override_qty=max(0, int(round(item.final_qty))),
            override_timestamp=now,
            reason_code=item.reason_code,
            reason_text=item.reason_text,
            output_language=req.output_language,
            recommendation_snapshot=snapshot,
            snapshot_hash=snapshot_hash(snapshot),
        ))
    saved = await _repo().save_draft_edits(AdjustDraftRequest(
        run_id=req.run_id, expected_run_version=req.expected_version, events=events,
    ))
    view = await _repo().get_run_view(req.run_id)
    return {
        **saved,
        "changed": len(events),
        "total_qty": view["total_qty"],
        "results": view["results"],
    }


@app.get("/api/runs/{run_id}/submission-readiness")
async def run_submission_readiness(run_id: str):
    return (await _repo().submission_readiness(run_id)).model_dump(mode="json")


@app.post("/api/runs/{run_id}/submit")
async def submit_run(run_id: str, body: VersionReq, request: Request):
    return await _repo().submit_and_lock(
        run_id, body.expected_version, NoopSubmissionAdapter(),
        submitted_by=request.state.subject,
    )


@app.get("/api/attribution/jobs/{job_id}")
async def attribution_job(job_id: str):
    return await _repo().get_job(job_id)


@app.get("/api/attribution/cases")
async def attribution_cases(
    status: CaseState | None = None,
    shop_code: str | None = None,
    goods_code: str | None = None,
    direction: str | None = Query(default=None, pattern="^(UP|DOWN)$"),
    date_from: str | None = None,
    date_to: str | None = None,
    job_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
):
    return await _repo().list_cases(
        state=status, shop_code=shop_code, goods_code=goods_code,
        direction=direction, date_from=date_from, date_to=date_to, job_id=job_id,
        limit=page_size, offset=(page - 1) * page_size,
    )


@app.get("/api/attribution/cases/{case_id}")
async def attribution_case(case_id: str):
    return await _repo().get_case(case_id)


@app.get("/api/attribution/cases/{case_id}/attempts/{attempt_number}/raw-log")
async def attribution_attempt_raw_log(case_id: str, attempt_number: int):
    events = await _repo().get_attempt_log(case_id, attempt_number)
    content = "\n".join(
        json.dumps(event, ensure_ascii=False, default=str, separators=(",", ":"))
        for event in events
    )
    if content:
        content += "\n"
    filename = f"attribution-{case_id}-attempt-{attempt_number}.jsonl"
    return Response(
        content=content,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/attribution/cases/{case_id}/reviews")
async def review_attribution_case(case_id: str, body: AttributionReviewReq, request: Request):
    contributions = [
        {
            **cause.model_dump(),
            "domain": cause.domain or (
                "seasonality" if cause.cause_code in {"SEASONAL_SHIFT", "HOLIDAY_EFFECT"}
                else "substitution" if cause.cause_code == "SUBSTITUTION_TRANSFER"
                else "manual"
            ),
        }
        for cause in body.causes or []
    ] or None
    knowledge_request = None
    if body.publish_knowledge:
        if not body.knowledge_scope:
            raise HTTPException(
                status_code=422,
                detail="knowledge scope is required when publishing",
            )
        expiry = body.knowledge_expires_at
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        # The scope label says which dimensions to pin; the values come from the
        # case, so an approved finding is bound to the store and SKU it was
        # observed on rather than becoming a global rule.
        case = await _repo().get_case(case_id)
        scope_label = body.knowledge_scope.upper()
        knowledge_request = KnowledgePublishRequest(
            kind=body.knowledge_kind,
            scope_shop_code=case.get("shop_code") if "SHOP" in scope_label else None,
            scope_goods_code=case.get("goods_code") if "SKU" in scope_label else None,
            scope_category=body.knowledge_category,
            applies_from=body.knowledge_applies_from,
            applies_to=body.knowledge_applies_to,
            prior_value=body.knowledge_prior_value,
            proposed_value=body.knowledge_proposed_value,
            evidence={"scope_label": body.knowledge_scope, "case_id": case_id,
                      "reviewer": request.state.subject},
            expires_at=expiry,
        )
    knowledge_decisions = None
    if body.knowledge_decisions:
        # The scope label decides which dimensions to pin; the values themselves
        # come from the case, so a reviewer can widen a rule to the category but
        # can never bind it to a store or SKU the case was not about.
        case = await _repo().get_case(case_id)
        # Falling back to SHOP_SKU would silently narrow a candidate the agent
        # proposed at category level, and a rejection recorded against the wrong
        # scope is worse than no rejection at all.
        candidate_scopes = {
            item.get("candidate_id"): item
            for item in ((case.get("latest_report") or {}).get("knowledge_candidates") or [])
        }
        knowledge_decisions = []
        for decision in body.knowledge_decisions:
            candidate = candidate_scopes.get(decision.candidate_id) or {}
            scope_label = (
                decision.scope_label or candidate.get("scope_label") or "SHOP_SKU").upper()
            category = decision.scope_category
            if category is None:
                category = (candidate.get("scope") or {}).get("category")
            expiry = decision.expires_at
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            knowledge_decisions.append(KnowledgeDecisionInput(
                candidate_id=decision.candidate_id,
                decision=decision.decision,
                cause_code=decision.cause_code,
                kind=decision.kind,
                domain=decision.domain,
                scope_label=scope_label,
                scope_shop_code=case.get("shop_code") if "SHOP" in scope_label else None,
                scope_goods_code=case.get("goods_code") if "SKU" in scope_label else None,
                scope_category=(category if "CATEGORY" in scope_label else None),
                applies_from=decision.applies_from,
                applies_to=decision.applies_to,
                prior_value=decision.prior_value,
                proposed_value=decision.proposed_value,
                condition=decision.condition,
                reject_reason=decision.reject_reason,
                note=decision.note,
                expires_at=expiry,
                evidence={"scope_label": scope_label, "case_id": case_id},
            ))
    await _repo().request_review(case_id, ReviewRequest(
        expected_case_version=body.expected_version,
        expected_report_version=body.expected_report_version,
        action=body.action,
        reviewer_subject=request.state.subject,
        notes=body.comment,
        contributions=contributions,
        summary=body.summary,
        knowledge_decisions=knowledge_decisions,
    ), publish_knowledge=knowledge_request)
    return await _repo().get_case(case_id)


@app.post("/api/attribution/cases/{case_id}/retry")
async def retry_attribution_case(case_id: str, body: RetryReq):
    await _repo().retry_case(
        case_id, body.expected_version, output_language=body.output_language)
    return await _repo().get_case(case_id)


@app.post("/api/attribution/cases/{case_id}/cancel")
async def cancel_attribution_case(case_id: str, body: VersionReq):
    await _repo().cancel_case(case_id, body.expected_version)
    return await _repo().get_case(case_id)


@app.get("/api/attribution/review-count")
async def attribution_review_count():
    return {"needs_review": await _repo().pending_review_count()}


@app.get("/api/attribution/diagnostic-agents")
async def attribution_diagnostic_agents():
    return {"items": list_diagnostic_agents()}


@app.get("/api/attribution/traces/{trace_id}")
async def attribution_trace(trace_id: str):
    return {"trace_id": trace_id, "events": await _repo().get_trace(trace_id)}


@app.get("/api/attribution/knowledge")
async def attribution_knowledge(
    shop_code: str | None = None,
    goods_code: str | None = None,
    status: str | None = Query(default=None, pattern="^(CANDIDATE|SHADOW|ACTIVE|RETIRED)$"),
    include_expired: bool = False,
):
    return {"items": await _repo().list_knowledge(
        shop_code=shop_code, goods_code=goods_code, status=status,
        include_expired=include_expired)}


@app.get("/api/attribution/knowledge/rejections")
async def attribution_knowledge_rejections(
    case_id: str | None = None,
    cause_code: str | None = None,
    reason_code: str | None = Query(
        default=None,
        pattern="^(WRONG_CAUSE|NOT_THE_DRIVER|WRONG_SCOPE|WRONG_MAGNITUDE|ONE_OFF_EVENT"
                "|INSUFFICIENT_EVIDENCE|ALREADY_KNOWN|OTHER)$"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    return {"items": await _repo().list_knowledge_rejections(
        case_id=case_id, cause_code=cause_code, reason_code=reason_code, limit=limit)}


@app.get("/api/attribution/knowledge/feedback")
async def attribution_knowledge_feedback():
    """What reviewers accepted and rejected, per cause and per reason."""
    return await _repo().knowledge_feedback_summary()


@app.get("/api/attribution/claims/feedback")
async def attribution_claim_feedback(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    shop_code: str | None = None,
):
    """How often the reason a store manager stated held up against the evidence.

    The counterpart to the knowledge report card: that one grades the diagnostic
    agents, this one grades the claim they were asked to check. A reason code
    that is almost never corroborated is either a habit or a cause the registry
    cannot yet express, and `out_of_scope_total` separates the two.
    """
    return await _repo().claim_verdict_summary(
        date_from=date_from, date_to=date_to, shop_code=shop_code)


@app.get("/api/attribution/knowledge/resolve")
async def attribution_knowledge_resolve(
    shop_code: str = Query(min_length=1),
    goods_code: str = Query(min_length=1),
    category: str | None = None,
    on_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """What the engine would apply for this line today, and how strongly."""
    return {"shop_code": shop_code, "goods_code": goods_code,
            "entries": await _repo().active_knowledge_for(
                shop_code, goods_code, category=category, on_date=on_date)}


@app.post("/api/attribution/outcomes/daily-sales")
async def ingest_daily_sales(body: OutcomeIngestRequest):
    """Accept the store's daily sales feed and rescore any window it closes."""
    return await _repo().ingest_daily_sales(body)


@app.get("/api/attribution/outcomes")
async def attribution_outcomes(
    shop_code: str | None = None,
    goods_code: str | None = None,
    status: str | None = Query(default=None, pattern="^(PENDING|PARTIAL|COMPLETE)$"),
    verdict: str | None = Query(
        default=None, pattern="^(PENDING|ENGINE_BETTER|HUMAN_BETTER|TIE)$"),
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await _repo().list_outcomes(
        shop_code=shop_code, goods_code=goods_code, status=status, verdict=verdict,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset)


@app.get("/api/attribution/accuracy")
async def attribution_accuracy(
    shop_code: str | None = None,
    goods_code: str | None = None,
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """The scoreboard the loop exists to move: engine error versus human error."""
    return await _repo().outcome_accuracy_summary(
        shop_code=shop_code, goods_code=goods_code, date_from=date_from, date_to=date_to)


# ---- Administrator console (guarded by the /api/admin/ prefix in require_api_auth) ----
@app.get("/api/admin/overview")
async def admin_overview():
    overview = await _repo().admin_overview()
    return overview | {
        "attribution_worker": _worker_status(),
        "forecast_pairs": len(FC),
        "agent_runtime": agent_status(),
    }


@app.get("/api/admin/jobs")
async def admin_jobs(page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=50, ge=1, le=100)):
    return await _repo().list_jobs(limit=page_size, offset=(page - 1) * page_size)


@app.get("/api/admin/review-queue")
async def admin_review_queue(status: CaseState | None = None,
                             page: int = Query(default=1, ge=1),
                             page_size: int = Query(default=50, ge=1, le=100)):
    return await _repo().review_queue(
        state=status, limit=page_size, offset=(page - 1) * page_size)


@app.post("/api/admin/attribution/cases/bulk-dismiss")
async def admin_bulk_dismiss(body: BulkDismissReq, request: Request):
    return await _repo().bulk_dismiss(
        [(item.case_id, item.expected_version) for item in body.cases],
        actor=request.state.subject, reason=body.reason,
    )

# ---- Agent-orchestrated path (Microsoft Agent Framework + Foundry) ----
@app.get("/api/agent/status")
def agent_status():
    if agent_runtime is None:
        return {"available": False, "sdk_installed": False, "endpoint_configured": False,
                "model_configured": False, "model": None, "reason": _AGENT_IMPORT_ERR or "agent runtime unavailable"}
    return agent_runtime.status()

@app.post("/api/replenish/agent")
async def run_agent_one(r: RunReq):
    if agent_runtime is None:
        return {"error": "agent runtime unavailable", "agent_unavailable": True, "reason": _AGENT_IMPORT_ERR}
    try:
        res = await agent_runtime.run_single(
            r.shop_code, r.goods_code, r.date, SKU_MAP, FC,
            await _live_knowledge(r.shop_code, r.goods_code, r.date))
        TRACES[res["trace_id"]] = res
        rec = await _record_run("agent", r.shop_code, [res], kind="single")
        return rec["results"][0]
    except agent_runtime.AgentUnavailable as e:
        return {"error": "agent unavailable", "agent_unavailable": True, "reason": str(e)}

@app.post("/api/replenish/agent/batch")
async def run_agent_batch(r: BatchReq):
    if agent_runtime is None:
        return {"error": "agent runtime unavailable", "agent_unavailable": True, "reason": _AGENT_IMPORT_ERR}
    try:
        out = await agent_runtime.run_batch(
            r.shop_code, r.date, SKU_MAP, FC,
            lambda code: _live_knowledge(r.shop_code, code, r.date))
        for res in out:
            TRACES[res["trace_id"]] = res
        if out:
            rec = await _record_run("agent", r.shop_code, out, kind="batch")
            return rec["results"]
        return []
    except agent_runtime.AgentUnavailable as e:
        return {"error": "agent unavailable", "agent_unavailable": True, "reason": str(e)}
