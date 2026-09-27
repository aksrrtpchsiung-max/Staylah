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
        "no furniture needed",
    )),
    ("eq", "fully", (
        "fully furnished", "full furnished", "fully-furnished",
        "fully renovated", "finely renovated", "move-in ready",
    )),
    ("eq", "partially", (
        "partially furnished", "partly furnished", "semi furnished", "semi-furnished",
        "some furniture", "semi-renovated",
    )),
    # When only "has furniture/furnished" is stated, it merely means "cannot be without furniture"; use neq so that fully furnished listings are not wrongly excluded.
    ("neq", "unfurnished", (
        "furnished", "has furniture", "with furniture", "furniture", "furnishings",
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
