"""In-process Microsoft Agent Framework (MAF) runner.

The LLM *orchestrates* the deterministic skills (routing / soft-info / safety+target /
final selection / explanation) as Foundry function tools, while the auditable math stays
in ``engine``/``skills``. This is the genuinely agent-driven path that the ``/api/replenish/agent``
endpoints expose next to the static algorithm engine.

Degrades gracefully: if ``agent-framework`` is not installed or Foundry creds are missing,
``status()`` reports why and ``run_single``/``run_batch`` raise ``AgentUnavailable`` so the
backend can keep serving the deterministic engine as default.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from threading import Lock
from typing import Annotated, Any


def _log(msg: str) -> None:
    """Timestamped progress line to stdout (visible in the backend terminal)."""
    print(f"[agent {datetime.now():%H:%M:%S}] {msg}", flush=True)

# --- config (.env) -----------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)
    load_dotenv(override=False)  # also honor CWD / process env
except Exception:  # dotenv optional
    pass

PROJECT_ENDPOINT = (os.getenv("FOUNDRY_PROJECT_ENDPOINT") or os.getenv("PROJECT_ENDPOINT") or "").strip()
MODEL_DEPLOYMENT = (
    os.getenv("FOUNDRY_MODEL_DEPLOYMENT")
    or os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
    or os.getenv("FOUNDRY_MODEL")
    or os.getenv("MODEL_DEPLOYMENT_NAME")
    or ""
).strip()
BATCH_CONCURRENCY = int(os.getenv("AGENT_BATCH_CONCURRENCY", "4") or "4")
MAX_SKUS = int(os.getenv("AGENT_MAX_SKUS", "0") or "0")  # 0 = all SKUs in shop
# Foundry token acquisition via Azure CLI can take >10s on Windows; the
# azure-identity default is 10s, which produces false "Failed to invoke the
# Azure CLI" errors even when `az account get-access-token` eventually succeeds.
AZURE_CLI_PROCESS_TIMEOUT = int(os.getenv("AZURE_CLI_PROCESS_TIMEOUT", "30") or "30")

# --- deterministic skills (always importable) --------------------------------
from engine import run as engine_run  # noqa: E402
from engine import FLOW_LEAD  # noqa: E402
from skills.algo import core  # noqa: E402
from skills.soft import factors  # noqa: E402
from skills.data import inventory_feed  # noqa: E402
import config  # noqa: E402  # service-level (fill rate) resolution

# --- MAF import (optional) ---------------------------------------------------
_IMPORT_ERR: str | None = None
try:
    from agent_framework.foundry import FoundryChatClient  # type: ignore
    try:
        from agent_framework import Agent  # type: ignore
    except Exception:  # older/newer layouts
        Agent = None  # type: ignore
    try:
        from azure.identity import AzureCliCredential, ManagedIdentityCredential  # type: ignore
    except Exception as _cred_err:  # credential lib missing
        AzureCliCredential = None  # type: ignore
        ManagedIdentityCredential = None  # type: ignore
        _IMPORT_ERR = f"azure-identity missing: {_cred_err!r}"
except Exception as _e:  # agent-framework not installed
    FoundryChatClient = None  # type: ignore
    Agent = None  # type: ignore
    AzureCliCredential = None  # type: ignore
    ManagedIdentityCredential = None  # type: ignore
    _IMPORT_ERR = f"agent-framework not installed: {_e!r}"

_CREDENTIAL_LOCK = Lock()
_SHARED_CREDENTIAL = None


def _use_managed_identity() -> bool:
    flag = (os.getenv("AZURE_USE_MANAGED_IDENTITY") or "").strip().lower()
    return flag in {"1", "true", "yes", "on"}

INSTRUCTIONS = (
    "# 角色\n"
    "你是**门店级补货编排智能体**,运行在生产环境的零售自动补货系统里。你是链路上唯一\"会思考\""
    "的大脑:负责场景研判、软信息结合、多候选择优、异常判定与可解释归因——而不是照单执行一条"
    "固定流水线。\n\n"
    "# 第一性原则(不可突破的底线)\n"
    "1. 底层算量是确定性的、可审计、可回测的。安全库存、目标库存、候选订货量等一切数字**只能来自"
    "工具**;严禁你自行心算、估算或改写任何数值。\n"
    "2. 你的价值在\"编排\"而非\"算量\":你决定调用哪些工具、按什么顺序、如何解读结果、在候选中如何"
    "择优、以及何时上报人工。\n\n"
    "# 可用工具(按需自主调用,不必每次全调、也不必固定顺序)\n"
    "- get_forecast:取该门店+商品的需求预测分布(均值/分位数/波动 std/促销系数/历史天数)。\n"
    "- soft_delta:把季节/节假日/促销/新品等语义软信息折算为需求系数 Δ。\n"
    "- safety_and_target:按门店服务水平与补货周期,计算动态安全库存与目标库存。\n"
    "- replenish:端到端确定性补货,产出多候选(解析解 / 蒙特卡洛)与安全/目标库存。\n\n"
    "# 工作方式(自主推理,拒绝机械化)\n"
    "1. **先建立事实**:调用 get_forecast,读懂需求水平、波动与不确定性,再决定后续策略。\n"
    "2. **研判场景**:判断该商品属于 标品稳定 / 生鲜短保 / 长尾间歇 / 新品冷启动 / 促销 / 节假日 / "
    "季节品 / 异常缺货 中的哪一类,并说明判据。不同场景的补货逻辑截然不同。\n"
    "3. **按需结合软信息**:仅在与场景相关时调用 soft_delta(如促销、节假日、季节、新品);"
    "标品淡季无软信息就不要强行叠加系数。\n"
    "4. **分层算力**:稳定标品用解析解即可;只有生鲜/促销/长尾/高波动/强约束等复杂场景才依赖蒙特卡洛"
    "候选(由 replenish 内部产出)。不要对简单品过度计算。\n"
    "5. **择优**:在 replenish 给出的候选中结合场景与软信息选出最优方案并给出理由——例如生鲜/促销"
    "倾向更高候选以防缺货,长尾/滞销倾向保守以防积压与损耗。\n"
    "6. **异常判定(人在环路)**:当建议量畸高/畸低/为 0、与预测严重背离,或你对结果信心不足时,"
    "明确标注\"需人工复核\"并说明触发原因;其余情况自动放行。\n\n"
    "# 输出要求\n"
    "面向门店计划员,用简洁中文给出:①最终补货数量;②关键归因(场景、软信息 Δ、安全/目标库存与在手的"
    "作用);③风险提示与是否需人工复核。结论要能直接执行。"
)


class AgentUnavailable(RuntimeError):
    """Raised when the agent path cannot run (missing SDK or Foundry creds)."""


def status() -> dict[str, Any]:
    credential_sdk = ManagedIdentityCredential if _use_managed_identity() else AzureCliCredential
    sdk = FoundryChatClient is not None and credential_sdk is not None
    endpoint_ok = bool(PROJECT_ENDPOINT)
    model_ok = bool(MODEL_DEPLOYMENT)
    available = sdk and endpoint_ok and model_ok
    if available:
        reason = "ready"
    elif not sdk:
        reason = _IMPORT_ERR or "agent-framework SDK not installed"
    elif not endpoint_ok and not model_ok:
        reason = "FOUNDRY_PROJECT_ENDPOINT 与 FOUNDRY_MODEL_DEPLOYMENT 未在 .env 配置"
    elif not endpoint_ok:
        reason = "FOUNDRY_PROJECT_ENDPOINT 未在 .env 配置"
    else:
        reason = "FOUNDRY_MODEL_DEPLOYMENT 未在 .env 配置"
    return {
        "available": available,
        "sdk_installed": sdk,
        "endpoint_configured": endpoint_ok,
        "model_configured": model_ok,
        "model": MODEL_DEPLOYMENT or None,
        "reason": reason,
    }


def _require_available() -> None:
    st = status()
    if not st["available"]:
        raise AgentUnavailable(st["reason"])


_STEP_META = {
    "get_forecast": ("预测获取", "agent.get_forecast", "algo"),
    "soft_delta": ("软信息Δ", "agent.soft_delta", "soft"),
    "safety_and_target": ("安全/目标库存", "agent.safety_and_target", "algo"),
    "replenish": ("确定性择优", "agent.replenish", "algo"),
}


def _steps_from_calls(calls: list[dict]) -> list[dict]:
    steps = []
    for i, c in enumerate(calls, start=1):
        name, skill, typ = _STEP_META.get(c["name"], (c["name"], c["name"], "algo"))
        steps.append({
            "step": i, "name": name, "skill": skill, "type": typ, "delta": c.get("delta", 0),
            "input": c.get("input", ""), "output": c.get("output", ""),
        })
    return steps


def _build_agent(client, tools):
    """Build an agent across MAF constructor variants (version-tolerant)."""
    if hasattr(client, "as_agent"):
        return client.as_agent(name="store-replenishment-agent", instructions=INSTRUCTIONS, tools=tools)
    if Agent is not None:
        return Agent(client=client, name="store-replenishment-agent", instructions=INSTRUCTIONS, tools=tools)
    raise AgentUnavailable("无法构建 MAF Agent:agent-framework 版本不兼容")


def _get_credential():
    """Reuse one credential so batch runs do not create a fresh auth flow per SKU."""
    global _SHARED_CREDENTIAL
    with _CREDENTIAL_LOCK:
        if _SHARED_CREDENTIAL is None:
            if _use_managed_identity():
                client_id = (
                    os.getenv("FOUNDRY_MANAGED_IDENTITY_CLIENT_ID")
                    or os.getenv("AZURE_CLIENT_ID")
                    or ""
                ).strip() or None
                _SHARED_CREDENTIAL = ManagedIdentityCredential(client_id=client_id)
            else:
                _SHARED_CREDENTIAL = AzureCliCredential(process_timeout=AZURE_CLI_PROCESS_TIMEOUT)
        return _SHARED_CREDENTIAL


async def run_single(shop_code: str, goods_code: str, date_str: str | None, sku_map: dict,
                     fc_index: dict, knowledge: list | None = None) -> dict:
    """Run the LLM-orchestrated agent for one shop+SKU and return an engine-shaped result."""
    _require_available()
    key = f"{shop_code}_{goods_code}"
    fc = fc_index.get(key)
    if not fc:
        raise AgentUnavailable(f"无预测数据: {key}")
    sku_info = sku_map[goods_code]
    dt = _parse_dt(date_str)
    category = sku_info["category"]
    flow = "A"
    lead_time = FLOW_LEAD[flow]
    # Resolved operational parameters (store default <- store/SKU override).
    rp = config.resolve(shop_code, goods_code)
    # Current inventory from the feed (auto-fetched, staff-editable).
    inv = inventory_feed.get_inventory(shop_code, goods_code, fc.get("mean", 0), dt.strftime("%Y-%m-%d"))
    on_hand = inv.get("on_hand", 0.0)
    calls: list[dict] = []
    t0 = time.time()
    _log(f"▶ SKU {goods_code}({sku_info.get('goods_name','')}) 门店{shop_code} 开始编排")

    # ---- tools (closures record each invocation for the trace) ----
    def get_forecast() -> dict:
        """获取该门店+商品的预测分布(mean/p50/p90/std/promo_uplift/days)。"""
        calls.append({"name": "get_forecast", "input": key,
                      "output": f"均值{fc['mean']} p90={fc['p90']} 促销系数x{fc.get('promo_uplift', 1)}"})
        _log(f"  [{goods_code}] tool get_forecast → 均值{fc['mean']} p90={fc['p90']}")
        return fc

    def soft_delta(
        promo_uplift: Annotated[float, "预测中的促销提升系数"],
        on_promo: Annotated[bool, "是否处于促销"],
        days: Annotated[int, "历史可用天数"],
    ) -> dict:
        """计算季节/节假日/促销/新品叠加后的软信息需求系数。"""
        out = factors.combine(category, dt, promo_uplift, on_promo, days)
        calls.append({"name": "soft_delta", "input": f"品类{category} 促销{on_promo}",
                      "output": f"总系数x{out['total_delta']}", "delta": round(out["total_delta"] - 1, 2)})
        return out

    def reorder_levels(
        daily_mean: Annotated[float, "日均需求"],
        daily_std: Annotated[float, "日需求标准差"],
        scenario: Annotated[str, "场景标签"],
    ) -> dict:
        """确定性再补货点(s)与目标库存(S)(使用门店/商品配置的服务水平与目标覆盖天数)。"""
        fr = rp["fill_rate"]; cov = int(rp.get("coverage", core.COVERAGE))
        z = core.z_from_fill_rate(fr)
        s = core.reorder_point(daily_mean, daily_std, z, lead_time)
        big_s = core.order_up_to(daily_mean, daily_std, z, lead_time, cov)
        calls.append({"name": "reorder_levels",
                      "input": f"均值{daily_mean} 场景{scenario} 服务水平{fr:.0%} 流程{flow}·提前期{lead_time}+覆盖{cov}天",
                      "output": f"再补点s={s} 目标S={big_s}"})
        return {"reorder_point": s, "order_up_to": big_s}

    def replenish() -> dict:
        """端到端确定性补货(连续检查 s,S):返回候选、最终建议量与补货点/目标库存。"""
        res = engine_run(shop_code, sku_info, fc, dt, flow=flow, knowledge=knowledge)
        calls.append({"name": "replenish", "input": f"净可用{res['position']} 流程{flow}",
                      "output": f"建议{res['chosen_qty']} 场景{res['scenario']} "
                                + ("已触发" if res.get("triggered") else "未触发")})
        return res

    tools = [get_forecast, soft_delta, reorder_levels, replenish]
    credential = _get_credential()
    client = FoundryChatClient(project_endpoint=PROJECT_ENDPOINT, model=MODEL_DEPLOYMENT, credential=credential)
    agent = _build_agent(client, tools)

    prompt = (
        f"任务:为门店 {shop_code} 的商品 {goods_code}({sku_info['goods_name']},品类 {category})"
        f"在 {dt:%Y-%m-%d} 做出当日补货决策。补货流程={flow}(在架提前期 {lead_time} 天),"
        f"当前在手库存 {on_hand}(净可用见库存工具)。\n"
        "请自主编排:先取预测建立事实,研判这是什么补货场景,按需结合软信息,计算再补货点/目标库存,"
        "判断库存是否已跌破再补货点(是则不定量补足到目标库存,否则今日不补),"
        "并判断是否属于需人工复核的异常。最后用一段中文给出最终数量、关键归因"
        "与风险提示。记住:所有数字必须来自工具,不要自行估算。"
    )
    try:
        result = await agent.run(prompt)
    except Exception as e:  # auth / model / network failures at run time
        raise AgentUnavailable(f"Agent 运行失败: {e}") from e

    llm_text = getattr(result, "text", None) or str(result)

    # Authoritative, auditable numbers always come from the deterministic engine;
    # the LLM contributes routing/selection reasoning + the natural-language explanation.
    res = dict(engine_run(shop_code, sku_info, fc, dt, flow=flow, knowledge=knowledge))
    steps = _steps_from_calls(calls) or res.get("steps", [])
    res.update({
        "engine": "agent",
        "explanation": llm_text.strip() or res.get("explanation", ""),
        "summary": llm_text.strip() or res.get("summary", ""),
        "steps": steps,
        "trace": steps,
    })
    return res


async def run_batch(shop_code: str, date_str: str | None, sku_map: dict, fc_index: dict,
                    knowledge_for: Any = None) -> list[dict]:
    """Run the agent for every SKU carried by a shop (bounded concurrency).

    ``knowledge_for`` is an async callable taking a goods code and returning that
    SKU's engine directives, so knowledge stays resolved per SKU rather than
    leaking one SKU's learned parameters across the whole store.
    """
    _require_available()
    codes = [k.split("_", 1)[1] for k in fc_index if k.split("_", 1)[0] == shop_code]
    if MAX_SKUS > 0:
        codes = codes[:MAX_SKUS]
    sem = asyncio.Semaphore(max(1, BATCH_CONCURRENCY))

    async def _one(code: str):
        async with sem:
            knowledge = await knowledge_for(code) if knowledge_for else None
            return await run_single(shop_code, code, date_str, sku_map, fc_index, knowledge)

    results = await asyncio.gather(*[_one(c) for c in codes], return_exceptions=True)
    out = [r for r in results if not isinstance(r, Exception)]
    if not out and results:
        # surface the first failure so the caller can report a clear reason
        first = next((r for r in results if isinstance(r, Exception)), None)
        if first is not None:
            raise AgentUnavailable(str(first))
    return out


def _parse_dt(s: str | None) -> datetime:
    if not s:
        return datetime(2025, 6, 1)
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime(2025, 6, 1)
