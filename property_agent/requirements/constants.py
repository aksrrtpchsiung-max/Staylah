"""Constants responsibilities extracted without changing behavior."""
from __future__ import annotations



MAX_INPUT_CHARS = 8000


QUERYABLE_LISTING_FIELDS = {
    "transaction_type",
    "price.amount",
    "price.currency",
    "price.period",
    "attributes.property_type",
    "attributes.unit_layout",
    "attributes.listing_scope",
    "attributes.area_sqft",
    "attributes.bathrooms",
    "attributes.room_type",
    "attributes.ensuite_bathroom",
    "attributes.owner_stays",
    "attributes.cooking_policy",
    "attributes.utilities_included",
    "attributes.wifi_included",
    "attributes.visitors_allowed",
    "attributes.pets_allowed",
    "attributes.furnishing",
    "attributes.tenure_type",
    "attributes.lease_years",
    "bedrooms",
    "listed_date",
}


LISTING_OPERATORS = {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "contains"}


FURNISHING_VALUES = frozenset({"fully", "partially", "unfurnished"})


FURNISHING_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("eq", "unfurnished", (
        "unfurnished", "not furnished", "no furniture", "without furniture",
        "无家具", "不带家具", "不配家具", "不要家具",
    )),
    ("eq", "fully", (
        "fully furnished", "full furnished", "fully-furnished",
        "家具齐全", "全装修", "精装修", "拎包入住",
    )),
    ("eq", "partially", (
        "partially furnished", "partly furnished", "semi furnished", "semi-furnished",
        "部分家具", "部分家私", "半装修",
    )),
    # 只说“有家具/带家具”时只是“不能没有家具”，用 neq 才不会误杀家具齐全的房源。
    ("neq", "unfurnished", (
        "带家具", "有家具", "配家具", "家具", "家私", "furnished", "furniture",
    )),
)


DERIVED_CATEGORIES = {"commute", "nearby_amenity", "environment", "accessibility"}


DERIVED_OPERATORS = {"eq", "lte", "gte", "between", "minimize", "maximize", "preferred"}


OPEN_REQUIREMENT_HANDLING = {"best_effort"}


PROFILE_FACT_FIELDS = {
    "household.occupant_count",
    "household.has_children",
    "household.planning_children",
    "occupant.workplace",
    "occupant.school",
}
