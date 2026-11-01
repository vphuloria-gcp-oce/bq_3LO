#!/usr/bin/env python3
"""
Deterministic Synthetic Data Generator for the BigQuery 3LO & Row-Level Security (RLS) Demo.

Generates 45 rows (15 rows per region = 3 stores x 5 weeks) of weekly retail store sales data.
Each region embeds a distinct, contrasting business storyline so that when queried by different
regional users (uno -> North, dos -> South, tres -> West) or synthesized by an AI Agent via 3LO,
each user receives completely isolated insights:

  - North (uno@<domain>): Surging high-margin Winter Apparel demand (+18% above target),
    driven by the VIP_WINTER_BUNDLE_10 loyalty campaign, low returns (~2.8%), 0 stockouts.
  - South (dos@<domain>): High unit volume from aggressive BOGO_50_CLEARANCE_BLITZ promos,
    but severe discount bleed compresses gross margins (~28%) and a defective adhesive batch
    on RunPro v4 sneakers spikes customer return rates (~16.8%).
  - West  (tres@<domain>): Strong customer foot traffic and healthy full-price margins (~50%),
    but missing weekly sales targets (~69% attainment) due to severe port/warehouse supply-chain
    stockouts (8-14 stockout incidents/week) on flagship Smart Home Hub SKUs.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

WEEKS = [
    "2026-09-28",
    "2026-10-05",
    "2026-10-12",
    "2026-10-19",
    "2026-10-26",
]

STORES = [
    # NORTH REGION (15 rows: 3 stores x 5 weeks)
    {
        "region": "North",
        "store_id": "STR-N01",
        "store_name": "Minneapolis Nicollet Flagship",
        "city": "Minneapolis",
        "state": "MN",
        "store_manager": "Elena Rostova",
        "top_category": "Winter Apparel & Outerwear",
        "active_promo": "VIP_WINTER_BUNDLE_10",
        "weekly_target_usd": 85000.00,
        "base_units": 690,
        "avg_unit_price": 148.0,
        "discount_rate": 0.055,
        "gross_margin_pct": 56.8,
        "base_traffic": 2450,
        "return_rate_pct": 2.4,
        "stockout_incidents": 0,
        "notes": [
            "Early cold front drove 22% surge in down parka bundles; zero stockouts across core sizes.",
            "VIP loyalty members accounted for 64% of gross sales; attachment of thermal accessories at record high.",
            "Exceeded weekly target by $14K; full-price sell-through on Alpine Pro jackets remains above 90%.",
            "Corporate group outfitting order boosted mid-week traffic; gross margin held steady at 57.2%.",
            "Fifth consecutive week beating target; pre-winter inventory buffer healthy at 98% in-stock rate.",
        ],
    },
    {
        "region": "North",
        "store_id": "STR-N02",
        "store_name": "Chicago Magnificent Mile Hub",
        "city": "Chicago",
        "state": "IL",
        "store_manager": "Marcus Vance",
        "top_category": "Winter Apparel & Outerwear",
        "active_promo": "VIP_WINTER_BUNDLE_10",
        "weekly_target_usd": 110000.00,
        "base_units": 890,
        "avg_unit_price": 152.0,
        "discount_rate": 0.060,
        "gross_margin_pct": 55.4,
        "base_traffic": 3300,
        "return_rate_pct": 2.9,
        "stockout_incidents": 0,
        "notes": [
            "Lakefront wind chill triggered strong weekend turnout; VIP_WINTER_BUNDLE_10 increased average basket to $218.",
            "Zero stockout incidents; automated safety-stock replenishment from Midwest DC arrived 1 day early.",
            "High-margin merino base layers and heated gloves led category growth; return rate well below 3%.",
            "Beat weekly sales target by 19%; concierge fitting appointments up 31% week-over-week.",
            "Closed October at 121% target attainment with industry-leading 56% gross margin.",
        ],
    },
    {
        "region": "North",
        "store_id": "STR-N03",
        "store_name": "Boston Back Bay Commons",
        "city": "Boston",
        "state": "MA",
        "store_manager": "Claire Beaumont",
        "top_category": "Winter Apparel & Outerwear",
        "active_promo": "VIP_WINTER_BUNDLE_10",
        "weekly_target_usd": 92000.00,
        "base_units": 740,
        "avg_unit_price": 149.0,
        "discount_rate": 0.052,
        "gross_margin_pct": 57.5,
        "base_traffic": 2720,
        "return_rate_pct": 2.6,
        "stockout_incidents": 0,
        "notes": [
            "University commuter demand lifted waterproof shell sales; net revenue finished 14% above target.",
            "Minimal promotional discounting needed; customers trading up to Gore-Tex flagship line.",
            "Store conversion rate reached 28.4%; zero out-of-stock events on top 25 winter SKUs.",
            "Weekend marathon spectators drove record accessory volume; net margin expanded 140 bps.",
            "Strongest week of the quarter (+20% vs target); customer satisfaction and loyalty signups at all-time high.",
        ],
    },
    # SOUTH REGION (15 rows: 3 stores x 5 weeks)
    {
        "region": "South",
        "store_id": "STR-S01",
        "store_name": "Austin Domain Lifestyle Center",
        "city": "Austin",
        "state": "TX",
        "store_manager": "Diego Morales",
        "top_category": "Athletic Footwear & Activewear",
        "active_promo": "BOGO_50_CLEARANCE_BLITZ",
        "weekly_target_usd": 90000.00,
        "base_units": 1020,
        "avg_unit_price": 94.0,
        "discount_rate": 0.245,
        "gross_margin_pct": 28.4,
        "base_traffic": 3400,
        "return_rate_pct": 15.8,
        "stockout_incidents": 1,
        "notes": [
            "BOGO_50_CLEARANCE_BLITZ drove high unit volume (1,020 units), but $23K in promo discounts pulled net revenue below target.",
            "Quality alert: customers returning RunPro v4 sneakers citing sole glue separation in humid weather (15.8% return rate).",
            "Heavy clearance traffic is overwhelming checkout staff while compressing store gross margin to 28.1%.",
            "Returned RunPro v4 inventory (lot #RP4-09) confirmed defective; net sales missed target by 16% after promo markdowns.",
            "Recommend halting BOGO_50_CLEARANCE_BLITZ immediately and quarantining RunPro v4 lot #RP4-09 to stop margin bleed.",
        ],
    },
    {
        "region": "South",
        "store_id": "STR-S02",
        "store_name": "Atlanta Midtown Peachtree",
        "city": "Atlanta",
        "state": "GA",
        "store_manager": "Simone Washington",
        "top_category": "Athletic Footwear & Activewear",
        "active_promo": "BOGO_50_CLEARANCE_BLITZ",
        "weekly_target_usd": 95000.00,
        "base_units": 1080,
        "avg_unit_price": 95.0,
        "discount_rate": 0.255,
        "gross_margin_pct": 27.2,
        "base_traffic": 3650,
        "return_rate_pct": 17.4,
        "stockout_incidents": 1,
        "notes": [
            "Gross sales topped $102K on heavy BOGO volume, but 25.5% promo discount rate cut net revenue to $76.4K (80% of target).",
            "Spike in footwear returns (17.1%) driven by RunPro v4 midsole delamination; customers requesting refunds over exchanges.",
            "Promo bargain hunters are cannibalizing full-price activewear; gross margin dropped to 26.9%.",
            "Customer service desk processed 188 footwear returns this week alone; net margin down 1,900 bps vs North region.",
            "Urgent need to replace BOGO clearance with targeted loyalty tiers and pull defective RunPro v4 batch from shelves.",
        ],
    },
    {
        "region": "South",
        "store_id": "STR-S03",
        "store_name": "Miami Brickell City Flagship",
        "city": "Miami",
        "state": "FL",
        "store_manager": "Carlos Mendoza",
        "top_category": "Athletic Footwear & Activewear",
        "active_promo": "BOGO_50_CLEARANCE_BLITZ",
        "weekly_target_usd": 105000.00,
        "base_units": 1160,
        "avg_unit_price": 96.0,
        "discount_rate": 0.260,
        "gross_margin_pct": 26.8,
        "base_traffic": 3900,
        "return_rate_pct": 18.5,
        "stockout_incidents": 2,
        "notes": [
            "High foot traffic (3,900 visitors) from BOGO signage, but $28.9K in promo giveaways eroded profitability.",
            "RunPro v4 sneaker returns hit 18.5% due to heat-sensitive sole adhesive failure; impacting store NPS.",
            "Despite selling 1,190+ units, net revenue missed weekly target by $18K due to stacked clearance coupons.",
            "Gross margin fell to 26.4%; store labor hours consumed by processing defective footwear warranty returns.",
            "South region summary: unit demand is strong, but BOGO discounting and RunPro v4 quality defects are crushing net revenue.",
        ],
    },
    # WEST REGION (15 rows: 3 stores x 5 weeks)
    {
        "region": "West",
        "store_id": "STR-W01",
        "store_name": "Seattle Pine Street Tech Hub",
        "city": "Seattle",
        "state": "WA",
        "store_manager": "Kenji Takahashi",
        "top_category": "Smart Home & Consumer Electronics",
        "active_promo": "FULL_PRICE_LAUNCH",
        "weekly_target_usd": 115000.00,
        "base_units": 410,
        "avg_unit_price": 205.0,
        "discount_rate": 0.030,
        "gross_margin_pct": 49.5,
        "base_traffic": 3100,
        "return_rate_pct": 3.2,
        "stockout_incidents": 9,
        "notes": [
            "High buyer intent for OmniHub Smart Display, but Port of Tacoma container backlog caused 8 SKU stockouts; missed target by 29%.",
            "Turned away ~140 customers seeking Smart Thermostat Pro; regional DC shipment delayed another 7 days.",
            "Gross margin remains strong at 49.8% on Minimal discounting, but 10 stockout incidents capped net revenue at $81.5K.",
            "Store foot traffic exceeded 3,150 visitors, yet conversion fell to 13% because hero Smart Home shelves are empty.",
            "Requesting emergency inter-regional transfer from Central warehouse; stockouts cost an estimated $34K in lost sales this week.",
        ],
    },
    {
        "region": "West",
        "store_id": "STR-W02",
        "store_name": "San Francisco Union Square",
        "city": "San Francisco",
        "state": "CA",
        "store_manager": "Priya Patel",
        "top_category": "Smart Home & Consumer Electronics",
        "active_promo": "FULL_PRICE_LAUNCH",
        "weekly_target_usd": 125000.00,
        "base_units": 430,
        "avg_unit_price": 210.0,
        "discount_rate": 0.028,
        "gross_margin_pct": 50.4,
        "base_traffic": 3450,
        "return_rate_pct": 3.1,
        "stockout_incidents": 12,
        "notes": [
            "11 stockout incidents across Smart Security Camera 3-Packs and OmniHub; attained only 70% of $125K weekly target.",
            "Customers willing to pay full retail price (50.4% margin), but West Coast distribution bottleneck left display tables without inventory.",
            "13 out-of-stock events recorded; raincheck waitlist now exceeds 260 local customers.",
            "Low return rate (3.0%) and high product satisfaction, but severe supply-chain starvation is crippling West region revenue.",
            "Closed week at 68% target attainment ($85.2K vs $125K target) solely due to 14 stockout incidents on top-selling electronics.",
        ],
    },
    {
        "region": "West",
        "store_id": "STR-W03",
        "store_name": "Los Angeles Grove Flagship",
        "city": "Los Angeles",
        "state": "CA",
        "store_manager": "Hannah Sterling",
        "top_category": "Smart Home & Consumer Electronics",
        "active_promo": "FULL_PRICE_LAUNCH",
        "weekly_target_usd": 130000.00,
        "base_units": 460,
        "avg_unit_price": 208.0,
        "discount_rate": 0.032,
        "gross_margin_pct": 49.2,
        "base_traffic": 3800,
        "return_rate_pct": 3.5,
        "stockout_incidents": 11,
        "notes": [
            "Strong weekend showroom traffic (3,800 visitors), but 10 hero SKU stockouts prevented us from capturing $38K in demand.",
            "Port of LA drayage delay held up 4 pallets of Smart Home Starter Kits; net sales reached only 71% of target.",
            "Zero promo margin erosion (49.3% gross margin), yet 12 stockout incidents forced sales associates to issue rainchecks.",
            "Competitor stores nearby capturing overflow demand while our top 5 Smart Home SKUs remain out of stock at Ontario DC.",
            "West region executive takeaway: demand and margins are healthy, but resolving West Coast supply-chain stockouts is Priority #1.",
        ],
    },
]

WEEK_MODIFIERS = [0.96, 0.99, 1.01, 1.03, 1.05]


def generate_rows() -> list[dict]:
    rows: list[dict] = []
    record_num = 1
    for store in STORES:
        for w_idx, week_date in enumerate(WEEKS):
            mod = WEEK_MODIFIERS[w_idx]
            if store["region"] == "West":
                unit_mod = 1.0 - (w_idx * 0.015)
                stockouts = store["stockout_incidents"] + (w_idx % 3)
            else:
                unit_mod = mod
                stockouts = store["stockout_incidents"]

            units_sold = int(round(store["base_units"] * unit_mod))
            gross_revenue = round(units_sold * store["avg_unit_price"], 2)
            discount_usd = round(gross_revenue * store["discount_rate"], 2)
            net_revenue = round(gross_revenue - discount_usd, 2)
            weekly_target = round(store["weekly_target_usd"], 2)
            target_attainment_pct = round((net_revenue / weekly_target) * 100.0, 1)
            foot_traffic = int(round(store["base_traffic"] * mod))
            margin_delta = (w_idx - 2) * 0.2
            gross_margin_pct = round(store["gross_margin_pct"] + margin_delta, 1)
            return_rate_pct = round(
                store["return_rate_pct"] + ((w_idx * 0.3) if store["region"] == "South" else (w_idx * 0.05)),
                1,
            )

            rows.append(
                {
                    "record_id": f"SLS-{record_num:03d}",
                    "week_start_date": week_date,
                    "region": store["region"],
                    "store_id": store["store_id"],
                    "store_name": store["store_name"],
                    "city": store["city"],
                    "state": store["state"],
                    "store_manager": store["store_manager"],
                    "top_category": store["top_category"],
                    "active_promo": store["active_promo"],
                    "foot_traffic": foot_traffic,
                    "units_sold": units_sold,
                    "gross_revenue_usd": gross_revenue,
                    "discount_usd": discount_usd,
                    "net_revenue_usd": net_revenue,
                    "weekly_target_usd": weekly_target,
                    "target_attainment_pct": target_attainment_pct,
                    "gross_margin_pct": gross_margin_pct,
                    "return_rate_pct": return_rate_pct,
                    "stockout_incidents": stockouts,
                    "manager_weekly_note": store["notes"][w_idx],
                }
            )
            record_num += 1
    return rows


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = generate_rows()

    csv_path = DATA_DIR / "weekly_store_sales.csv"
    jsonl_path = DATA_DIR / "weekly_store_sales.jsonl"
    schema_path = DATA_DIR / "weekly_store_sales_schema.json"

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    bq_schema = [
        {"name": "record_id", "type": "STRING", "mode": "REQUIRED", "description": "Unique weekly store record identifier"},
        {"name": "week_start_date", "type": "DATE", "mode": "REQUIRED", "description": "Monday start date of the retail sales week"},
        {"name": "region", "type": "STRING", "mode": "REQUIRED", "description": "Geographic sales region (North, South, West) used for Row-Level Security (RLS)"},
        {"name": "store_id", "type": "STRING", "mode": "REQUIRED", "description": "Store identifier code"},
        {"name": "store_name", "type": "STRING", "mode": "REQUIRED", "description": "Retail store display name"},
        {"name": "city", "type": "STRING", "mode": "REQUIRED", "description": "Store city"},
        {"name": "state", "type": "STRING", "mode": "REQUIRED", "description": "Store two-letter state code"},
        {"name": "store_manager", "type": "STRING", "mode": "REQUIRED", "description": "Store general manager full name"},
        {"name": "top_category", "type": "STRING", "mode": "REQUIRED", "description": "Highest-volume product category in the region"},
        {"name": "active_promo", "type": "STRING", "mode": "REQUIRED", "description": "Active promotional campaign code"},
        {"name": "foot_traffic", "type": "INT64", "mode": "REQUIRED", "description": "Weekly store visitor count"},
        {"name": "units_sold", "type": "INT64", "mode": "REQUIRED", "description": "Total retail units sold during the week"},
        {"name": "gross_revenue_usd", "type": "FLOAT64", "mode": "REQUIRED", "description": "Gross retail revenue before promotional discounts (USD)"},
        {"name": "discount_usd", "type": "FLOAT64", "mode": "REQUIRED", "description": "Total promotional markdowns and discounts applied (USD)"},
        {"name": "net_revenue_usd", "type": "FLOAT64", "mode": "REQUIRED", "description": "Net realized revenue after discounts (USD)"},
        {"name": "weekly_target_usd", "type": "FLOAT64", "mode": "REQUIRED", "description": "Weekly net revenue target assigned to the store (USD)"},
        {"name": "target_attainment_pct", "type": "FLOAT64", "mode": "REQUIRED", "description": "Net revenue as a percentage of weekly_target_usd"},
        {"name": "gross_margin_pct", "type": "FLOAT64", "mode": "REQUIRED", "description": "Realized gross margin percentage after product cost and promos"},
        {"name": "return_rate_pct", "type": "FLOAT64", "mode": "REQUIRED", "description": "Percentage of sold units returned by customers"},
        {"name": "stockout_incidents", "type": "INT64", "mode": "REQUIRED", "description": "Number of hero SKU out-of-stock events recorded during the week"},
        {"name": "manager_weekly_note", "type": "STRING", "mode": "REQUIRED", "description": "Qualitative commentary from the store manager explaining weekly performance drivers"},
    ]
    with schema_path.open("w", encoding="utf-8") as f:
        json.dump(bq_schema, f, indent=2)

    print(f"✅ Generated {len(rows)} rows across {len({r['region'] for r in rows})} regions:")
    for region in ["North", "South", "West"]:
        reg_rows = [r for r in rows if r["region"] == region]
        avg_att = sum(r["target_attainment_pct"] for r in reg_rows) / len(reg_rows)
        avg_margin = sum(r["gross_margin_pct"] for r in reg_rows) / len(reg_rows)
        avg_ret = sum(r["return_rate_pct"] for r in reg_rows) / len(reg_rows)
        tot_stockouts = sum(r["stockout_incidents"] for r in reg_rows)
        print(
            f"   • {region:<5} ({len(reg_rows)} rows): "
            f"Avg Target Attainment={avg_att:5.1f}%, "
            f"Avg Margin={avg_margin:4.1f}%, "
            f"Avg Return Rate={avg_ret:4.1f}%, "
            f"Total Stockouts={tot_stockouts}"
        )
    print(f"📄 CSV:    {csv_path}")
    print(f"📄 JSONL:  {jsonl_path}")
    print(f"📄 Schema: {schema_path}")


if __name__ == "__main__":
    main()
