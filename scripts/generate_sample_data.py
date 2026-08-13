"""生成 M1 样例数据（符合 mart 口径的模拟 CSV）。

字段名采用《低评分归因与改善建议Agent搭建思路》的标准命名：
- is_late_delivery / late_days / delivery_variance_days
- has_review_record / is_low_score / is_strict_negative_score
- is_delivery_analysis_eligible / primary_category_name / primary_payment_type
- order_month / route

数据用固定 seed 生成，保证可复现；并注入真实业务模式：
延迟订单的低评分率高于按时订单，且延迟越久低评分率越高，
使 5 个基准问题具有有意义的结论，便于对账测试。

用法: uv run python scripts/generate_sample_data.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
OUT_DIR = Path(__file__).resolve().parent.parent / "sample_data"

STATES = ["SP", "RJ", "MG", "RS", "BA", "PR", "SC"]
CATEGORIES = [
    "health_beauty", "bed_bath_table", "sports_leisure",
    "furniture_decor", "computers_accessories", "toys_games",
]
PAYMENTS = ["credit_card", "boleto", "voucher", "debit_card"]

N_ORDERS = 1000

# 延迟天数分档的边界（左闭右开）
BUCKETS = [("1-3天", 1, 4), ("4-7天", 4, 8), ("8-14天", 8, 15), ("15天+", 15, 40)]


def delay_bucket(days: int) -> str:
    if days <= 0:
        return "按时"
    for name, lo, hi in BUCKETS:
        if lo <= days < hi:
            return name
    return "15天+"


def pick_score(is_late: bool, late_days: int) -> int:
    """按延迟状态生成 review_score，注入'延迟越多低评分越高'的模式。"""
    if not is_late:
        # 按时：评分偏高
        return random.choices([5, 4, 3, 2, 1], weights=[50, 28, 12, 6, 4])[0]
    # 延迟：分档越严重，低分权重越高
    base_low = {0: 25, 1: 35, 2: 50, 3: 70, 4: 85}  # 按时/1-3/4-7/8-14/15+
    tier = 0 if late_days < 1 else (
        1 if late_days < 4 else 2 if late_days < 8 else 3 if late_days < 15 else 4
    )
    w_low = base_low[tier]
    return random.choices(
        [5, 4, 3, 2, 1],
        weights=[100 - w_low, 0, w_low // 2, w_low // 2, max(0, w_low - w_low // 2 * 2)],
    )[0]


def main() -> None:
    rng = random.Random(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    order_rows = []
    seller_rows = []
    start = date(2017, 1, 1)

    for i in range(1, N_ORDERS + 1):
        order_id = f"ORD{i:06d}"
        state = rng.choice(STATES)
        category = rng.choice(CATEGORIES)
        payment = rng.choice(PAYMENTS)
        price = round(rng.uniform(30, 800), 2)
        freight = round(rng.uniform(10, 150), 2)
        payment_value = round(price + freight, 2)

        # 35% 订单延迟
        is_late = 1 if rng.random() < 0.35 else 0
        late_days = 0 if not is_late else rng.randint(1, 25)
        # 配送日期差：延迟为正，提前为负
        variance_days = late_days if is_late else -rng.randint(1, 5)
        bucket = delay_bucket(late_days)

        review_score = pick_score(bool(is_late), late_days)
        has_review = 1
        is_low = 1 if review_score <= 3 else 0
        is_strict_neg = 1 if review_score <= 2 else 0

        purchase = start + timedelta(days=rng.randint(0, 500))
        approval_days = round(rng.uniform(0.2, 3.0), 2)
        fulfillment_days = round(approval_days + rng.uniform(2, 12), 2)

        order_rows.append([
            order_id, "delivered", purchase.isoformat(), purchase.strftime("%Y-%m"),
            state, category, payment,
            payment_value, price, freight, review_score, has_review, is_low,
            is_strict_neg, is_late, late_days, variance_days, bucket,
            1, approval_days, fulfillment_days,
        ])

        # 订单-卖家级：85% 单卖家，15% 双卖家
        n_sellers = 2 if rng.random() < 0.15 else 1
        seller_state = rng.choice(STATES)
        for _ in range(n_sellers):
            s_id = f"SELL{rng.randint(1, 300):03d}"
            s_price = round(price / n_sellers, 2)
            s_freight = round(freight / n_sellers, 2)
            seller_rows.append([
                order_id, s_id, seller_state, state, f"{seller_state}→{state}",
                1 if seller_state != state else 0, 1, s_price, s_freight,
                is_late, is_low, 1 if n_sellers > 1 else 0,
            ])

    # 写入 CSV
    order_path = OUT_DIR / "mart_order_delivery.csv"
    order_fields = [
        "order_id", "order_status", "order_purchase_timestamp", "order_month",
        "customer_state", "primary_category_name", "primary_payment_type",
        "payment_value", "price_total", "freight_total", "review_score",
        "has_review_record", "is_low_score", "is_strict_negative_score",
        "is_late_delivery", "late_days", "delivery_variance_days", "delay_bucket",
        "is_delivery_analysis_eligible", "approval_days", "fulfillment_days",
    ]
    with open(order_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(order_fields)
        w.writerows(order_rows)

    seller_path = OUT_DIR / "mart_order_seller_delivery.csv"
    seller_fields = [
        "order_id", "seller_id", "seller_state", "customer_state", "route",
        "cross_state", "seller_items", "seller_price", "seller_freight",
        "is_late_delivery", "is_low_score", "is_multi_seller_order",
    ]
    with open(seller_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(seller_fields)
        w.writerows(seller_rows)

    print(f"mart_order_delivery: {len(order_rows)} 行 -> {order_path}")
    print(f"mart_order_seller_delivery: {len(seller_rows)} 行 -> {seller_path}")

    # 打印关键汇总，便于对账
    elig = [r for r in order_rows if r[18] == 1]  # is_delivery_analysis_eligible
    n = len(elig)
    delay_rate = sum(r[14] for r in elig) / n * 100   # is_late_delivery
    low_rate = sum(r[12] for r in elig) / n * 100     # is_low_score
    print(f"有效样本 {n}，总体延迟率 {delay_rate:.1f}%，低评分率 {low_rate:.1f}%")
    buckets = {}
    for r in elig:
        buckets.setdefault(r[17], []).append(r)       # delay_bucket
    for name in ["按时", "1-3天", "4-7天", "8-14天", "15天+"]:
        rows = buckets.get(name, [])
        if rows:
            print(f"  {name}: n={len(rows)} 低评分率 {sum(x[12] for x in rows)/len(rows)*100:.1f}%")


if __name__ == "__main__":
    main()
