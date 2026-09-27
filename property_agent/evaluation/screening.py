"""Screening responsibilities extracted without changing behavior."""
from __future__ import annotations
from property_agent.contracts import ConversationProfile, Listing, ScreenResult, ScreenedListing
from property_agent.evaluation.validation import _validate_listing, _validate_profile


def screen(listings: list[Listing], profile: ConversationProfile) -> ScreenResult:
    """Compatibility wrapper retained only for the v0 interface; property eligibility is no longer determined by C."""
    _validate_profile(profile)
    eligible: list[ScreenedListing] = []
    seen: set[str] = set()
    for index, listing in enumerate(listings):
        _validate_listing(listing, f"listings[{index}]")
        key = listing["listing_key"]
        if key not in seen:
            eligible.append({"listing_key": key, "checks": []})
            seen.add(key)

    return {
        "profile_version": profile["version"],
        "eligible": eligible,
        "rejected": [],
        "needs_verification": [],
    }
