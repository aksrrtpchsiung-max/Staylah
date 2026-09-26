const sampleCards = [
  {
    listing_key: "sample-clementi",
    rank: 1,
    title: "A bright corner in Clementi",
    price: { amount: 3200, currency: "SGD", period: "month" },
    bedrooms: 2,
    attributes: { bathrooms: 2, area_sqft: 850, property_type: "hdb" },
    reasons: ["Space to settle in, with everyday essentials nearby."],
    tradeoffs: ["Commute time needs verification."],
    unknowns: ["Availability has not been verified."],
  },
  {
    listing_key: "sample-dover",
    rank: 2,
    title: "An easy-going home in Dover",
    price: { amount: 3500, currency: "SGD", period: "month" },
    bedrooms: 2,
    attributes: { bathrooms: 1, area_sqft: 780, property_type: "condo" },
    reasons: ["A calm setting for your next chapter."],
    tradeoffs: ["A smaller floor plan."],
    unknowns: ["Illustrative property, not a real listing."],
  },
];
function sampleResults() {
  if (document.querySelector(".cards")) return;
  message("user", "Looks good, find my home.");
  render({
    assistant_response: "A couple of places to picture your next chapter.",
    recommendation: {
      summary: "A couple of places to picture your next chapter.",
      limitations: ["Sample homes and prices for interface preview only."],
    },
    cards: sampleCards,
  });
}
