"""Forecast cache generator (emulates the dmall_promotion_forecasting sub-system).

Reads the history store-SKU CSV and precomputes a per shop x SKU demand
forecast distribution (mean / p50 / p90) plus promo uplift + weekday profile.
Output: store_replenishment/forecasting_cache/forecast_index.json (compact)
        store_replenishment/forecasting_cache/skus.json, shops.json
This stands in for the upstream forecasting batch run (precomputed mode).
"""
import csv, json, os, statistics, math
from collections import defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(ROOT, "..", "..", "agent_poc", "input_data", "wm_microsoft_poc_0610.csv")
OUT = os.path.join(ROOT, "..", "forecasting_cache")
os.makedirs(OUT, exist_ok=True)

skus, shops = {}, {}
qty = defaultdict(list)          # (shop,sku) -> [sale_qty]
promo_qty = defaultdict(list)    # (shop,sku) -> qty on promo days
base_qty = defaultdict(list)     # (shop,sku) -> qty on non-promo days

with open(CSV, encoding="utf-8") as f:
    r = csv.DictReader(f)
    for row in r:
        s, k = row["shop_code"], row["goods_code"]
        shops[s] = {"shop_code": s, "shop_name": row["shop_name"], "city": row["city"]}
        skus[k] = {"goods_code": k, "goods_name": row["goods_name"], "category": row["category"]}
        q = float(row["sale_qty"])
        qty[(s, k)].append(q)
        (promo_qty if row["if_promotion"] == "1" else base_qty)[(s, k)].append(q)

index = {}
for (s, k), arr in qty.items():
    n = len(arr); mean = sum(arr) / n if n else 0
    sd = statistics.pstdev(arr) if n > 1 else 0
    nb = base_qty[(s, k)]; npq = promo_qty[(s, k)]
    bmean = sum(nb) / len(nb) if nb else mean
    pmean = sum(npq) / len(npq) if npq else mean
    uplift = round(pmean / bmean, 2) if bmean > 0 else 1.0
    index[f"{s}_{k}"] = {
        "shop": s, "sku": k, "mean": round(mean, 2),
        "p50": round(mean, 2), "p90": round(mean + 1.28 * sd, 2),
        "std": round(sd, 2), "promo_uplift": min(uplift, 5.0), "days": n,
    }

json.dump(index, open(os.path.join(OUT, "forecast_index.json"), "w", encoding="utf-8"), ensure_ascii=False)
json.dump(list(skus.values()), open(os.path.join(OUT, "skus.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
json.dump(list(shops.values()), open(os.path.join(OUT, "shops.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"shops={len(shops)} skus={len(skus)} pairs={len(index)}")
