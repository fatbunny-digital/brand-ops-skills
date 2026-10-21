#!/usr/bin/env python3
"""Offline package checks, using Python 3.10+ standard library only.

This validates this repository's deliberately simple metadata layout, local file
links and synthetic data invariants. It is not a general YAML parser, model
behavior evaluator, fact checker or complete credential scanner.
"""

import csv
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "market-insights": "市场洞察",
    "product-planning": "商品企划",
    "content-marketing": "内容推广",
    "customer-care": "客户经营",
    "business-review": "经营复盘",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def quoted_field(text, key):
    match = re.search(rf"^  {re.escape(key)}: (\".*\")$", text, re.MULTILINE)
    require(match is not None, f"Missing quoted interface field: {key}")
    return json.loads(match.group(1))


def check_skills():
    actual = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    require(actual == set(EXPECTED), f"Expected exactly five skill folders, got {actual}")
    for slug, display in EXPECTED.items():
        folder = ROOT / "skills" / slug
        body = (folder / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(.*?)\n---\n(.+)", body, re.DOTALL)
        require(match is not None, f"Missing frontmatter/body: {slug}")
        front, instructions = match.groups()
        require(re.search(rf"^name: {re.escape(slug)}$", front, re.MULTILINE),
                f"Skill name differs from folder: {slug}")
        desc = re.search(r"^description: (.+)$", front, re.MULTILINE)
        require(desc and len(desc.group(1)) <= 1024, f"Missing/long description: {slug}")
        require(instructions.strip(), f"Empty instructions: {slug}")
        require("[TODO:" not in body, f"Unfinished scaffold: {slug}")
        metadata = (folder / "agents" / "openai.yaml").read_text(encoding="utf-8")
        require(quoted_field(metadata, "display_name") == display,
                f"Wrong Chinese display name: {slug}")
        require(25 <= len(quoted_field(metadata, "short_description")) <= 64,
                f"Short description outside 25–64 characters: {slug}")
        require(f"${slug}" in quoted_field(metadata, "default_prompt"),
                f"Default prompt does not invoke skill: {slug}")
        require("allow_implicit_invocation: false" not in metadata,
                f"Implicit discovery unexpectedly disabled: {slug}")
    return len(EXPECTED)


def check_links():
    checked = 0
    for file in ROOT.rglob("*.md"):
        if ".git" in file.relative_to(ROOT).parts:
            continue
        text = file.read_text(encoding="utf-8")
        for raw in re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", text):
            target = raw.strip().strip("<>")
            parts = urlsplit(target)
            if parts.scheme or parts.netloc or not parts.path:
                continue
            resolved = (file.parent / unquote(parts.path)).resolve()
            require(resolved.is_relative_to(ROOT), f"Link escapes repository: {file}: {raw}")
            require(resolved.exists(), f"Missing relative target: {file}: {raw}")
            if file.is_relative_to(ROOT / "skills"):
                skill_root = ROOT / "skills" / file.relative_to(ROOT / "skills").parts[0]
                require(resolved.is_relative_to(skill_root),
                        f"Skill depends on a file outside its install folder: {file}: {raw}")
            checked += 1
    return checked


def rows(name):
    with (ROOT / "examples" / "demo-brand" / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def money(row, field):
    return Decimal(row[field])


def check_demo():
    summary = rows("weekly-summary.csv")
    require(len(summary) == 2, "Expected two comparable demo weeks")
    contributions = []
    for row in summary:
        require(row["currency"] == "CNY" and row["timezone"] == "Asia/Shanghai",
                "Unexpected demo currency/timezone")
        revenue = (money(row, "discounted_product_revenue")
                   + money(row, "shipping_revenue") - money(row, "refund_amount"))
        costs = sum(money(row, f) for f in (
            "product_cost", "payment_fees", "packing_cost", "fulfillment_shipping"))
        before = revenue - costs
        after = before - money(row, "ad_spend")
        contributions.append((before, after))
        require(money(row, "payment_fees") == money(row, "discounted_product_revenue") * Decimal("0.03"),
                "Demo payment fee differs from stated 3%")
        require(money(row, "packing_cost") == money(row, "paid_orders") * 2,
                "Demo packing differs from stated per-order cost")
        require(money(row, "fulfillment_shipping") == money(row, "paid_orders") * 10,
                "Demo shipping differs from stated per-order cost")
    require(contributions == [(Decimal("2676"), Decimal("1476")),
                              (Decimal("2898"), Decimal("1098"))],
            f"Demo contribution examples drifted: {contributions}")
    channels = rows("channel-summary.csv")
    require(len(channels) == 2, "Expected two channel summaries")
    for week, channel, expected_roas in zip(summary, channels, (Decimal("3"), Decimal("2.5"))):
        require(week["period_start"] == channel["period_start"] and
                week["period_end"] == channel["period_end"], "Channel period mismatch")
        require(week["ad_spend"] == channel["spend"], "Channel spend mismatch")
        require(money(channel, "platform_attributed_revenue") / money(channel, "spend") == expected_roas,
                "Demo ROAS drifted")
    products = rows("products.csv")
    towel = next(p for p in products if p["sku"] == "TOWEL-01")
    discounted = money(towel, "regular_price_cny") * Decimal("0.9")
    contribution = discounted - money(towel, "product_cost_cny") - discounted * Decimal("0.03") - 2 - 10
    require(contribution == Decimal("45.3"), "Discount contribution drifted")
    for product in products:
        require(product["sales_window_start"] == "2026-10-06" and
                product["sales_window_end"] == "2026-10-19", "Inventory sales window mismatch")
        coverage = money(product, "sellable_stock") * 14 / money(product, "units_sold_last_14_days")
        require(coverage < money(product, "lead_time_days"), "Expected demo stock risk disappeared")
    eligible = [row["customer_alias"] for row in rows("customer-segments.csv")
                if row["marketing_status"] == "opted_in" and row["open_issue"] == "none"
                and int(row["last_marketing_days_ago"]) >= 7]
    require(eligible == ["C001"], f"Demo recipient conditions drifted: {eligible}")
    require(len(rows("customer-feedback.csv")) == 5, "Demo feedback sample changed")
    return len(summary), len(products), len(eligible)


def main():
    skill_count = check_skills()
    link_count = check_links()
    weeks, products, eligible = check_demo()
    print(f"PASS: {skill_count} skills and Chinese interface metadata")
    print(f"PASS: {link_count} relative file links and self-contained skill references")
    print(f"PASS: demo amounts, {weeks} reporting weeks, {products} SKU risks, {eligible} eligible customer")
    print("Scope: offline package/data checks; no model execution or live platform verification.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, StopIteration, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
