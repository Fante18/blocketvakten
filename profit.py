"""Pure business calculations for resale opportunities."""

from __future__ import annotations

from datetime import datetime, timezone

RISK_WORDS = (
    "trasig", "defekt", "reservdel", "sökes", "sokes", "reparation",
    "ej testad", "inte testad", "skadad", "parts", "broken",
)


def _number(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_profit(fields: dict) -> dict:
    """Calculate expected and actual economics without raising on empty fields."""
    purchase_value = fields.get("purchase_price")
    if purchase_value in (None, ""):
        purchase_value = fields.get("price")
    expected_value = fields.get("expected_resale_price")
    if expected_value in (None, ""):
        expected_value = fields.get("resale_price")
    purchase = _number(purchase_value)
    expected_sale = _number(expected_value)
    transport = _number(fields.get("transport_cost"))
    repair = _number(fields.get("repair_cost"))
    selling_fee = _number(fields.get("selling_fee"))
    other = _number(fields.get("other_cost"))
    labor = _number(fields.get("labor_cost"))
    expected_cost = purchase + transport + repair + selling_fee + other + labor
    expected_profit = expected_sale - expected_cost if expected_sale > 0 else None

    actual_sale = fields.get("actual_sale_price")
    actual_profit = None
    actual_cost = None
    if actual_sale not in (None, ""):
        actual_sale = _number(actual_sale)
        actual_transport = _number(fields.get("actual_transport_cost")) if fields.get("actual_transport_cost") not in (None, "") else transport
        actual_repair = _number(fields.get("actual_repair_cost")) if fields.get("actual_repair_cost") not in (None, "") else repair
        actual_fee = _number(fields.get("actual_selling_fee")) if fields.get("actual_selling_fee") not in (None, "") else selling_fee
        actual_other = _number(fields.get("actual_other_cost")) if fields.get("actual_other_cost") not in (None, "") else other
        actual_cost = purchase + actual_transport + actual_repair + actual_fee + actual_other + labor
        actual_profit = actual_sale - actual_cost

    def pct(value, denominator):
        return round((value / denominator) * 100, 1) if denominator is not None and denominator > 0 and value is not None else None

    return {
        "total_expected_cost": round(expected_cost, 2),
        "expected_profit": round(expected_profit, 2) if expected_profit is not None else None,
        "expected_margin_pct": pct(expected_profit, expected_sale),
        "expected_roi_pct": pct(expected_profit, expected_cost),
        "break_even_price": round(expected_cost, 2),
        "total_actual_cost": round(actual_cost, 2) if actual_cost is not None else None,
        "actual_profit": round(actual_profit, 2) if actual_profit is not None else None,
        "actual_margin_pct": pct(actual_profit, actual_sale) if actual_sale else None,
        "actual_roi_pct": pct(actual_profit, actual_cost),
    }


def risk_for_listing(listing: dict) -> dict:
    text = " ".join(str(listing.get(k) or "") for k in ("title", "notes")).casefold()
    hits = [word for word in RISK_WORDS if word in text]
    if len(hits) >= 2:
        level = "high"
    elif hits:
        level = "medium"
    else:
        level = "low"
    return {"level": level, "warnings": hits}


def deal_score(listing: dict, market_avg=None, market_count=0) -> dict:
    """Return a conservative 0-100 score; unknown values remain preliminary."""
    price = _number(listing.get("price"), 0)
    metrics = calculate_profit(listing)
    reference = _number(listing.get("expected_resale_price", listing.get("resale_price")), 0)
    if reference <= 0:
        reference = _number(market_avg, 0)
    discount = ((reference - price) / reference * 100) if reference > 0 and price > 0 else None
    risk = risk_for_listing(listing)
    score = 35.0
    if discount is not None:
        score += max(-20, min(35, discount * 1.4))
    if metrics["expected_profit"] is not None:
        score += max(-15, min(25, metrics["expected_profit"] / max(price, 1) * 35))
    if market_count < 3:
        score -= 8
    if risk["level"] == "medium":
        score -= 12
    elif risk["level"] == "high":
        score -= 28
    age = listing.get("first_seen_at")
    if age:
        try:
            dt = datetime.fromisoformat(str(age))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hours = max(0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
            score += max(-8, 5 - hours / 24)
        except (TypeError, ValueError):
            pass
    return {
        "deal_score": round(max(0, min(100, score)), 1),
        "market_reference": round(reference, 2) if reference > 0 else None,
        "discount_pct": round(discount, 1) if discount is not None else None,
        "preliminary": not bool(listing.get("expected_resale_price", listing.get("resale_price"))),
        "reliability": "low" if market_count < 3 else ("medium" if market_count < 10 else "high"),
        "risk_level": risk["level"],
        "risk_warnings": risk["warnings"],
    }


def enrich_listing(listing: dict, market_avg=None, market_count=0) -> dict:
    result = dict(listing)
    result.update(calculate_profit(result))
    result.update(deal_score(result, market_avg, market_count))
    result["actual_days_to_sell"] = None
    if result.get("bought_at") and result.get("sold_at"):
        try:
            bought = datetime.fromisoformat(str(result["bought_at"]))
            sold = datetime.fromisoformat(str(result["sold_at"]))
            result["actual_days_to_sell"] = max(0, (sold - bought).days)
        except (TypeError, ValueError):
            pass
    return result
