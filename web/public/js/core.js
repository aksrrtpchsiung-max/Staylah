const $ = (s) => document.querySelector(s);
const state = {
  session: null,
  mode: "preview",
  busy: false,
  sample: false,
  cards: new Map(),
  selected: new Map(),
  favoritePending: new Set(),
  retry: null,
  active: null,
  conversationId: null,
  conversations: [],
};
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const button = (text, cls, action) => {
  const n = el("button", cls, text);
  n.type = "button";
  n.onclick = action;
  return n;
};
const value = (v) =>
  Array.isArray(v)
    ? v.join(" – ")
    : typeof v === "boolean"
      ? v
        ? "Yes"
        : "No"
      : String(v ?? "Not specified");
const claim = (x) =>
  typeof x === "string" ? x : x.text || x.statement || x.description || "";
const money = (amount) =>
  typeof amount === "number"
    ? new Intl.NumberFormat("en-SG", { maximumFractionDigits: 0 }).format(
        amount,
      )
    : null;
const labels = {
  "price.amount": "Budget",
  "price.currency": "Currency",
  "price.period": "Payment",
  bedrooms: "Bedrooms",
  "attributes.listing_scope": "Space",
  "attributes.property_type": "Property",
  "attributes.ensuite_bathroom": "Ensuite bathroom",
  "attributes.area_sqft": "Area · sqft",
  "attributes.furnishing": "Furnishing",
};
const friendly = (v) =>
  ({
    whole_unit: "Whole unit",
    month: "Monthly",
    week: "Weekly",
    total: "Total price",
    commute: "Commute",
    room: "Private room",
    hdb: "HDB",
    condo: "Condo",
    rent: "Rent",
    buy: "Buy",
  })[v] || value(v).replaceAll("_", " ");
