"""Presentation responsibilities extracted without changing behavior."""
from __future__ import annotations
import json
from typing import Any



def _profile_summary(profile: dict[str, Any]) -> str:
    """Convert the confirmed candidate fields into a concise and verifiable English summary."""

    parts = ["Rent" if profile.get("intent") == "rent" else "Buy"]
    constraints = profile.get("listing_constraints", [])
    by_field = {item["field_path"]: item for item in constraints}
    budget = by_field.get("price.amount")
    if budget is not None:
        currency = by_field.get("price.currency", {}).get("value", "SGD")
        period = by_field.get("price.period", {}).get("value")
        period_text = {
            "month": "Monthly budget",
            "week": "Weekly budget",
            "total": "Total budget",
        }.get(period, "Budget")
        operator_text = {
            "lte": "up to",
            "lt": "below",
            "gte": "at least",
            "gt": "above",
            "eq": "of",
            "between": "around",
        }.get(budget["operator"], "of")
        parts.append(f"{period_text} {operator_text} {currency} {budget['value']}")
    scope = by_field.get("attributes.listing_scope")
    if scope is not None:
        parts.append({"whole_unit": "Whole unit", "room": "Private room", "bedspace": "Bedspace"}.get(
            scope["value"], f"Rental scope: {scope['value']}"
        ))
    bedrooms = by_field.get("bedrooms")
    if bedrooms is not None:
        operator_text = {"gte": "At least ", "lte": "At most ", "eq": ""}.get(
            bedrooms["operator"], ""
        )
        parts.append(f"{operator_text}{bedrooms['value']} bedroom(s)")
    property_type = by_field.get("attributes.property_type")
    if property_type is not None:
        values = property_type["value"] if isinstance(property_type["value"], list) else [property_type["value"]]
        labels = {"hdb": "HDB", "condo": "condo", "landed": "landed property", "apartment": "apartment"}
        parts.append("Property type: " + ", ".join(labels.get(value, str(value)) for value in values))
    ensuite = by_field.get("attributes.ensuite_bathroom")
    if ensuite is not None and ensuite.get("value") is True:
        parts.append("Ensuite bathroom required")
    for item in constraints:
        field = item["field_path"]
        if field in {
            "price.amount", "price.currency", "price.period", "attributes.listing_scope",
            "bedrooms", "attributes.property_type", "attributes.ensuite_bathroom",
        }:
            continue
        parts.append(f"{field}: {json.dumps(item['value'], ensure_ascii=False)}")
    for item in profile.get("derived_data_requirements", []):
        category, target, metric = item["category"], item.get("target"), item["metric"]
        if category == "accessibility" and metric == "residential_area":
            relation = "near " if item.get("value") == "near" else "in "
            parts.append(f"Preferred location: {relation}{target}")
        elif category == "commute" and metric == "travel_time":
            if item.get("value") is not None:
                parts.append(f"Commute to {target} within {item['value']} minutes")
            else:
                parts.append(f"Convenient commute to {target}")
        elif category == "nearby_amenity" and target == "school":
            parts.append("Schools nearby")
        elif category == "nearby_amenity" and target == "bus_stop":
            parts.append("Near a bus stop")
        elif category == "environment" and metric == "noise_level":
            parts.append("Quiet surroundings preferred")
        else:
            parts.append(f"{metric}: {item.get('value')}")
    for item in profile.get("open_data_requirements", []):
        parts.append(f"Best effort: {item['description']}")
    return "; ".join(parts)
