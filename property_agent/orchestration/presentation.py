"""Render existing recommendation text."""
from typing import Any

def _format_recommendation(state: dict[str, Any]) -> str:
    recommendation = state.get("published_recommendation") or {}
    snapshot = state.get("listing_snapshot") or {}
    by_key = {
        item["listing_key"]: item for item in snapshot.get("items") or []
    }
    lines: list[str] = []
    summary = (recommendation.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    for item in recommendation.get("ordered_items") or []:
        listing = by_key.get(item["listing_key"]) or {}
        title = listing.get("title") or item["listing_key"]
        reasons = [
            claim.get("text")
            for claim in item.get("reasons") or []
            if isinstance(claim, dict) and claim.get("text")
        ]
        suffix = f" — {'; '.join(reasons)}" if reasons else ""
        lines.append(f"{item.get('rank', '?')}. {title}{suffix}")
    limitations = recommendation.get("limitations") or []
    if limitations:
        lines.append("Limitations: " + "; ".join(str(item) for item in limitations))
    return "\n".join(lines)
