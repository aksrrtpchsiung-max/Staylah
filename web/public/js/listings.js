function listingPhotoSources(c) {
  const observations = (c.evidence || [])
    .filter(
      (entry) =>
        entry?.field === "media.search_card_photos" &&
        entry.value?.version === 1,
    )
    .sort(
      (a, b) =>
        (Date.parse(b.observed_at) || 0) - (Date.parse(a.observed_at) || 0),
    );
  const images = observations[0]?.value?.images;
  if (!Array.isArray(images)) return [];
  const photos = images.filter((image) => image?.kind === "photo");
  const fallback = images.filter((image) => image?.kind === "thumbnail");
  const urls = [];
  for (const image of photos.length ? [...photos, ...fallback] : fallback) {
    for (const candidate of Array.isArray(image.urls) ? image.urls : []) {
      try {
        const url = new URL(candidate);
        if (["https:", "http:"].includes(url.protocol) && !urls.includes(url.href))
          urls.push(url.href);
      } catch {}
    }
  }
  return urls;
}
function card(c) {
  state.cards.set(c.listing_key, c);
  const n = el("article", "card");
  n.dataset.key = c.listing_key;
  const art = el("div", "card-art");
  art.append(
    el("div", "building"),
    el("span", "rank", `#${c.rank || "–"} recommended`),
    el("span", "art-label", "ARCHITECTURAL ILLUSTRATION"),
  );
  const photoSources = listingPhotoSources(c);
  if (photoSources.length) {
    const image = document.createElement("img");
    image.className = "card-photo";
    image.alt = `${c.title || "Home"} listing photo`;
    image.loading = "lazy";
    image.decoding = "async";
    let sourceIndex = 0;
    image.onload = () => art.classList.add("has-photo");
    image.onerror = () => {
      sourceIndex += 1;
      if (sourceIndex < photoSources.length) image.src = photoSources[sourceIndex];
      else image.remove();
    };
    image.src = photoSources[sourceIndex];
    art.append(image);
  }
  const select = button("♡", "select-home", () => toggle(c));
  select.setAttribute("aria-label", `Save ${c.title || "home"}`);
  select.setAttribute("aria-pressed", "false");
  art.append(select);
  n.append(art);
  const content = el("div", "card-content");
  content.append(el("h3", "", c.title || "Home details unavailable"));
  const a = c.attributes || {};
  content.append(
    el(
      "div",
      "facts",
      [
        c.bedrooms != null ? `${c.bedrooms} bed` : null,
        a.bathrooms != null ? `${a.bathrooms} bath` : null,
        a.area_sqft != null ? `${a.area_sqft} sqft` : null,
        a.property_type ? friendly(a.property_type) : null,
      ]
        .filter(Boolean)
        .join(" · ") || "Property details not supplied",
    ),
  );
  const amount = money(c.price?.amount);
  const price = el(
    "div",
    "price",
    amount
      ? `${c.price.currency === "SGD" ? "S$" : c.price.currency + " "}${amount}`
      : "Price unavailable",
  );
  if (amount)
    price.append(
      el(
        "small",
        "",
        { month: "/ month", week: "/ week", total: "total" }[c.price.period] ||
          "",
      ),
    );
  content.append(price);
  if (c.reasons?.length)
    content.append(el("p", "reason", c.reasons.map(claim).join(" · ")));
  if (c.tradeoffs?.length || c.unknowns?.length) {
    const detail = el("details", "details");
    detail.append(el("summary", "", "A closer look"));
    for (const x of [...(c.tradeoffs || []), ...(c.unknowns || [])])
      detail.append(el("p", "", claim(x)));
    content.append(detail);
  }
  if (c.source_url) {
    try {
      const url = new URL(c.source_url);
      if (["https:", "http:"].includes(url.protocol)) {
        const link = el("a", "source-link", "View listing ↗");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        content.append(link);
      }
    } catch {}
  }
  n.append(content);
  return n;
}
function results(parent, data) {
  const list = [...(data.cards || [])];
  const toolbar = el("div", "results-toolbar");
  toolbar.append(
    el(
      "strong",
      "",
      `${list.length} ${list.length === 1 ? "home" : "homes"} to explore`,
    ),
  );
  const sorts = el("div", "sorts");
  const grid = el("div", "cards");
  function draw(mode) {
    grid.replaceChildren();
    const ordered = [...list].sort(
      mode === "price"
        ? (a, b) =>
            (a.price?.amount ?? Infinity) - (b.price?.amount ?? Infinity)
        : (a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity),
    );
    ordered.forEach((c) => grid.append(card(c)));
    syncSelection();
  }
  ["Recommended", "Lowest price"].forEach((name, i) =>
    sorts.append(
      button(name, i === 0 ? "active" : "", (e) => {
        sorts
          .querySelectorAll("button")
          .forEach((n) => n.classList.remove("active"));
        e.currentTarget.classList.add("active");
        draw(i ? "price" : "rank");
      }),
    ),
  );
  toolbar.append(sorts);
  parent.append(toolbar, grid);
  draw("rank");
  if (data.recommendation?.limitations?.length)
    parent.append(
      el("p", "limits", data.recommendation.limitations.join(" · ")),
    );
}
