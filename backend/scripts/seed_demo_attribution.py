"""Seed a demo set of attribution cases that exercises every reviewer-visible path.

The engine can only quantify a cause when the seed files under
``attribution/seeds/`` actually carry a factor for that shop, SKU and date.
Picking an arbitrary store/SKU/date therefore tends to produce the honest
"no verifiable cause" outcome, which is correct but shows none of the
decomposition a reviewer is meant to judge.

The scenarios below are chosen against those seeds so a demo walks through:

  1. multi    - two causes quantified, only a small unexplained remainder
  2. partial  - one cause quantified, one applicable but missing seed data
  3. single   - a single clean seasonal driver
  4. none     - the system refusing to invent an explanation

Run it against a live backend::

    cd store_replenishment/backend
    .venv/Scripts/python scripts/seed_demo_attribution.py

Attribution runs through the real agent, so expect roughly a minute per case.
Use ``--no-wait`` to enqueue and return immediately.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_USERNAME = "dmall"
DEFAULT_PASSWORD = "dmalltest"

TERMINAL_STATES = {"NEEDS_REVIEW", "APPROVED", "REJECTED", "FAILED", "CANCELLED", "SUPERSEDED"}


@dataclass(frozen=True)
class Scenario:
    """One demo case: a run, an override, and what the reviewer should learn."""

    key: str
    title: str
    shop_code: str
    goods_code: str
    decision_date: str
    delta: int
    reason_code: str
    reason_text: str
    teaches: str
    expected_recommended: int | None = None
    expected_causes: tuple[str, ...] = field(default=())


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        key="multi",
        title="完整归因：两条证据解释了绝大部分差异",
        shop_code="1028",
        goods_code="653270",
        decision_date="2027-01-01",
        delta=12,
        reason_code="SEASONAL",
        reason_text=(
            "元旦当天客流会涨，速冻云吞又是冬季主推品，系统给的量基本合适，"
            "我再往上加一点做安全垫。"
        ),
        teaches="冬季 + 元旦两条证据都能被数据核验，未解释量很小——这是最理想的归因输出。",
        expected_recommended=426,
        expected_causes=("SEASONAL_SHIFT", "HOLIDAY_EFFECT"),
    ),
    Scenario(
        key="partial",
        title="部分解释：有证据成立，但仍有缺口，且季节性缺数据",
        shop_code="1028",
        goods_code="653270",
        decision_date="2026-10-01",
        delta=70,
        reason_code="SEASONAL",
        reason_text="国庆第一天备货，另一个规格缺货顾客会转买这款，需要往上加。",
        teaches=(
            "节假日成立并算出数量；季节性虽被判定适用但系统没有 10 月的因子数据，"
            "会明确标记 EVIDENCE_UNAVAILABLE_FOR_CAUSE 而不是估一个数。"
        ),
        expected_recommended=390,
        expected_causes=("HOLIDAY_EFFECT",),
    ),
    Scenario(
        key="single",
        title="单因归因：夏季季节性",
        shop_code="1028",
        goods_code="128347",
        decision_date="2026-07-29",
        delta=30,
        reason_code="SEASONAL",
        reason_text="夏天酸梅汤走得快，最近每天都提前卖空，想多备一些。",
        teaches="只有一条证据成立时，归因不会被稀释成多个似是而非的原因。",
        expected_recommended=150,
        expected_causes=("SEASONAL_SHIFT",),
    ),
    Scenario(
        key="none",
        title="对照组：没有可核验的原因",
        shop_code="1028",
        goods_code="653270",
        decision_date="2026-07-29",
        delta=-50,
        reason_code="DEMAND_CHANGE",
        reason_text="感觉最近这款卖不动了，先少订一点。",
        teaches="店长给了理由，但数据里找不到任何支撑，系统如实说明而不是编造原因。",
        expected_recommended=252,
        expected_causes=(),
    ),
)


class ApiError(RuntimeError):
    pass


class Client:
    """Minimal JSON client so the script has no third-party dependencies."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: str | None = None

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(
                f"{method} {path} failed: {exc.reason}. Is the backend running at {self.base_url}?"
            ) from exc
        return json.loads(raw) if raw else {}

    def login(self, username: str, password: str) -> None:
        payload = self.request("POST", "/api/auth/login",
                               {"username": username, "password": password})
        self.token = payload["access_token"]


def seed_scenario(client: Client, scenario: Scenario) -> dict:
    """Produce a recommendation, override it, and return the enqueued case."""
    result = client.request("POST", "/api/replenish/run", {
        "shop_code": scenario.shop_code,
        "goods_code": scenario.goods_code,
        "date": scenario.decision_date,
    })
    if result.get("error") or not result.get("run_id"):
        raise ApiError(f"[{scenario.key}] engine returned no line for "
                       f"{scenario.shop_code}/{scenario.goods_code} on {scenario.decision_date}: "
                       f"{result.get('error') or result}")
    run_id = str(result["run_id"])
    recommended = int(result.get("chosen_qty") or result.get("final_qty") or 0)
    if scenario.expected_recommended is not None and recommended != scenario.expected_recommended:
        print(f"  ! recommendation drifted: expected {scenario.expected_recommended}, "
              f"got {recommended}. Seeds or forecasts changed; decomposition may differ.")

    override = max(0, recommended + scenario.delta)
    if override == recommended:
        raise ApiError(f"[{scenario.key}] override equals the recommendation, no case would open")

    adjusted = client.request("POST", "/api/replenish/adjust", {
        "run_id": run_id,
        "output_language": "zh-CN",
        "items": [{
            "sku": scenario.goods_code,
            "final_qty": float(override),
            "reason_code": scenario.reason_code,
            "reason_text": scenario.reason_text,
            "event_id": str(uuid.uuid4()),
        }],
    })
    case_ids = adjusted.get("case_ids") or []
    if not case_ids:
        raise ApiError(f"[{scenario.key}] adjust did not open a case: {adjusted}")
    return {"scenario": scenario, "run_id": run_id, "case_id": case_ids[0],
            "recommended": recommended, "override": override}


def wait_for_case(client: Client, case_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        case = client.request("GET", f"/api/attribution/cases/{case_id}")
        if case.get("state") in TERMINAL_STATES:
            return case
        if time.monotonic() >= deadline:
            return case
        time.sleep(5)


def describe(case: dict) -> list[str]:
    """Summarise the decomposition the reviewer will see in the UI."""
    lines: list[str] = []
    report = case.get("latest_report") or {}
    if not report:
        return ["      (尚无归因报告)"]

    bare = report.get("bare_baseline_qty")
    if bare is not None:
        lines.append(f"      去掉全部证据后的基准: {bare} 件")

    allocations = report.get("allocations") or []
    quantified = [row for row in allocations
                  if abs(float(row.get("signed_contribution_qty") or 0)) >= 0.005]
    if quantified:
        for row in quantified:
            weight = float(row.get("absolute_contribution_weight") or 0) * 100
            lines.append(f"      {row.get('label') or row.get('cause_code')}: "
                         f"{float(row['signed_contribution_qty']):+.0f} 件 ({weight:.1f}%)")
    else:
        lines.append("      (无可核验证据 - 全部差异待人工确认)")

    residual = report.get("unexplained_signed_gap")
    if residual is not None:
        lines.append(f"      未解释: {float(residual):+.0f} 件")
    coverage = report.get("coverage_ratio")
    if coverage is not None:
        lines.append(f"      证据覆盖率: {float(coverage) * 100:.1f}%")

    flags = report.get("risk_flags") or []
    if flags:
        lines.append(f"      风险标记: {', '.join(str(flag) for flag in flags)}")

    candidates = report.get("knowledge_candidates") or []
    if candidates:
        acceptable = sum(1 for item in candidates if item.get("acceptable"))
        lines.append(f"      知识候选: {len(candidates)} 条，其中 {acceptable} 条可采纳")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--only", action="append", choices=[s.key for s in SCENARIOS],
                        help="seed only the named scenario (repeatable)")
    parser.add_argument("--no-wait", action="store_true",
                        help="enqueue the cases and exit without waiting for attribution")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="seconds to wait per case (default: 300)")
    args = parser.parse_args()

    # The Windows console is often cp1252; never let an encode error kill a seeded run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    selected = [s for s in SCENARIOS if not args.only or s.key in args.only]
    client = Client(args.base_url)
    client.login(args.username, args.password)

    seeded: list[dict] = []
    failures: list[str] = []
    for scenario in selected:
        print(f"\n[{scenario.key}] {scenario.title}")
        print(f"  门店 {scenario.shop_code} / 商品 {scenario.goods_code} / {scenario.decision_date}")
        try:
            record = seed_scenario(client, scenario)
        except ApiError as exc:
            print(f"  x {exc}")
            failures.append(scenario.key)
            continue
        print(f"  推荐 {record['recommended']} -> 下单 {record['override']} "
              f"({record['override'] - record['recommended']:+d})")
        print(f"  case {record['case_id']}")
        seeded.append(record)

    if seeded and not args.no_wait:
        print("\nWaiting for attribution to finish (the agent takes ~1 min per case)...")
        for record in seeded:
            case = wait_for_case(client, record["case_id"], args.timeout)
            record["case"] = case
            print(f"  [{record['scenario'].key}] {case.get('state')}")

    print("\n" + "=" * 72)
    print("Demo cases")
    print("=" * 72)
    for record in seeded:
        scenario = record["scenario"]
        print(f"\n[{scenario.key}] {scenario.title}")
        print(f"  推荐 {record['recommended']} -> 下单 {record['override']}")
        print(f"  演示要点: {scenario.teaches}")
        print(f"  URL: /attribution/cases/{record['case_id']}")
        case = record.get("case")
        if case:
            print(f"  状态: {case.get('state')}")
            for line in describe(case):
                print(line)

    if failures:
        print(f"\nFailed scenarios: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
