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
const SIDEBAR_DEFAULT_WIDTH = 260;
const SIDEBAR_MAX_WIDTH = SIDEBAR_DEFAULT_WIDTH * 2;
const SIDEBAR_STORAGE_KEY = "staylah-sidebar-width";
function applySidebarWidth(width, persist = false) {
  const next = Math.round(
    Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_DEFAULT_WIDTH, width)),
  );
  const contentStart = next + 40;
  document.documentElement.style.setProperty("--sidebar-width", `${next}px`);
  document.documentElement.style.setProperty(
    "--content-start",
    `${contentStart}px`,
  );
  document.documentElement.style.setProperty(
    "--content-half",
    `${contentStart / 2}px`,
  );
  const handle = $("#sidebar-resize-handle");
  handle.setAttribute("aria-valuenow", String(next));
  handle.setAttribute("aria-valuetext", `${next} pixels`);
  if (persist) {
    try {
      localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
    } catch {}
  }
  return next;
}
function setupSidebarResize() {
  const handle = $("#sidebar-resize-handle");
  let width = SIDEBAR_DEFAULT_WIDTH;
  try {
    width = Number(localStorage.getItem(SIDEBAR_STORAGE_KEY)) || width;
  } catch {}
  width = applySidebarWidth(width);
  let startX = 0;
  let startWidth = width;
  let dragging = false;
  handle.addEventListener("pointerdown", (event) => {
    if (matchMedia("(max-width: 1050px)").matches) return;
    dragging = true;
    startX = event.clientX;
    startWidth = width;
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("sidebar-resizing");
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    width = applySidebarWidth(startWidth + event.clientX - startX);
  });
  const stopDragging = (event) => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("sidebar-resizing");
    if (handle.hasPointerCapture(event.pointerId))
      handle.releasePointerCapture(event.pointerId);
    width = applySidebarWidth(width, true);
  };
  handle.addEventListener("pointerup", stopDragging);
  handle.addEventListener("pointercancel", stopDragging);
  handle.addEventListener("dblclick", () => {
    width = applySidebarWidth(SIDEBAR_DEFAULT_WIDTH, true);
  });
  handle.addEventListener("keydown", (event) => {
    const changes = {
      ArrowLeft: width - 16,
      ArrowRight: width + 16,
      Home: SIDEBAR_DEFAULT_WIDTH,
      End: SIDEBAR_MAX_WIDTH,
    };
    if (!(event.key in changes)) return;
    event.preventDefault();
    width = applySidebarWidth(changes[event.key], true);
  });
}
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
async function api(path, payload, signal = AbortSignal.timeout(15000)) {
  const response = await fetch(path, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Session-ID": state.session || "",
    },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || "Please try again.");
  return body;
}
function resetConversationView() {
  state.cards.clear();
  state.selected.clear();
  state.favoritePending.clear();
  state.retry = null;
  state.sample = false;
  $("#messages").replaceChildren();
  $("#welcome").hidden = false;
  $("#error").hidden = true;
  $("#input").value = "";
  renderSelection();
}
function renderConversationList() {
  const list = $("#conversation-list");
  list.replaceChildren();
  for (const conversation of state.conversations) {
    const title = conversation.title || "Untitled conversation";
    const item = button("", "current-chat", () =>
      switchConversation(conversation.conversation_id));
    item.classList.toggle(
      "active",
      conversation.conversation_id === state.conversationId,
    );
    const favoriteCount = Number(conversation.favorite_count) || 0;
    item.title = favoriteCount
      ? `${title} · ${favoriteCount} saved`
      : title;
    item.setAttribute(
      "aria-label",
      favoriteCount ? `${title}, ${favoriteCount} saved` : title,
    );
    item.append(el("span", "history-title", title));
    if (favoriteCount) {
      const favorite = el("span", "history-favorite", "♥");
      favorite.setAttribute("aria-hidden", "true");
      item.append(favorite);
    }
    list.append(item);
  }
}
async function refreshConversations() {
  const data = await api("/api/conversations", {});
  state.conversations = data.conversations || [];
  renderConversationList();
  return state.conversations;
}
function restoreHistory(history) {
  resetConversationView();
  for (const item of history || []) {
    if (item.role === "assistant" && item.render_data?.recommendation)
      render(item.render_data);
    else if (["user", "assistant"].includes(item.role) && item.text)
      message(item.role, item.text);
  }
}
function restoreFavorites(favorites) {
  state.selected.clear();
  for (const favorite of favorites || []) {
    const card = state.cards.get(favorite.listing_key);
    if (card) state.selected.set(favorite.listing_key, card);
  }
  syncSelection();
}
async function init(conversationId = null) {
  try {
    const r = await api("/api/session", {
      conversation_id: conversationId,
    });
    state.session = r.session_id;
    state.conversationId = r.conversation_id;
    state.mode = r.mode;
    restoreHistory(r.history);
    restoreFavorites(r.favorites);
    await refreshConversations();
  } catch (e) {
    showError(e.message);
  }
}
async function switchConversation(conversationId) {
  if (state.busy || conversationId === state.conversationId) return;
  state.session = null;
  await init(conversationId);
}
async function bootstrap() {
  try {
    const conversations = await refreshConversations();
    await init(conversations[0]?.conversation_id || null);
  } catch (e) {
    showError(e.message);
  }
}
function showError(text) {
  $("#error").textContent = text;
  $("#error").hidden = false;
}
function scrollEnd() {
  window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
}
function message(role, text) {
  $("#welcome").hidden = true;
  const n = el("article", `message ${role}`);
  if (role === "assistant") {
    const label = el("div", "assistant-label");
    label.append(el("span", "", "⌂"), document.createTextNode("StayLah"));
    n.append(label);
    if (text) n.append(el("div", "body-text", text));
  } else n.textContent = text;
  $("#messages").append(n);
  return n;
}
function busy(on) {
  state.busy = on;
  $("#send").disabled = on;
  $("#send").hidden = on;
  $("#stop").hidden = !on;
  $("#stop").disabled = false;
  $("#stop").textContent = "■ Stop";
  $("#mobile-new").disabled = on;
  $("#preview").disabled = on;
  $("#input").disabled = on;
  $("#new-chat").disabled = on;
  document
    .querySelectorAll(
      ".confirmation-actions button, .clarification-controls button, .clarification-controls input, .favorite-list button",
    )
    .forEach((x) => (x.disabled = on));
}
function renderFavoriteList() {
  const populate = (list) => {
    list.replaceChildren();
    if (!state.selected.size) {
      list.append(el("p", "favorite-empty", "Save a home with ♡"));
      return;
    }
    state.selected.forEach((card, key) => {
      const item = el("div", "favorite-item");
      const jump = button(card.title || key, "favorite-jump", () => {
        const target = [...document.querySelectorAll("[data-key]")].find(
          (node) => node.dataset.key === key,
        );
        if (target)
          target.scrollIntoView({ behavior: "smooth", block: "center" });
        $("#mobile-favorites").open = false;
      });
      jump.title = card.title || key;
      const remove = button("♥", "favorite-remove", () => toggle(card));
      remove.setAttribute("aria-label", `Remove ${card.title || "home"}`);
      remove.disabled = state.favoritePending.has(key) || state.busy;
      item.append(jump, remove);
      list.append(item);
    });
  };
  populate($("#favorite-list"));
  populate($("#mobile-favorite-list"));
  $("#mobile-favorite-count").textContent = state.selected.size;
}
function renderSelection() {
  const wrap = $("#selection");
  wrap.replaceChildren();
  wrap.hidden = !state.selected.size;
  renderFavoriteList();
  if (!state.selected.size) return;
  wrap.append(
    el("span", "selection-caption", `${state.selected.size} saved`),
  );
  state.selected.forEach((c, key) =>
    wrap.append(
      button(`${c.title || key} ×`, "", () => toggle(c)),
    ),
  );
}
function syncSelection() {
  document.querySelectorAll("[data-key]").forEach((n) => {
    const on = state.selected.has(n.dataset.key);
    const pending = state.favoritePending.has(n.dataset.key);
    n.classList.toggle("selected", on);
    const control = n.querySelector(".select-home");
    if (!control) return;
    control.disabled = pending;
    control.setAttribute("aria-pressed", String(on));
    control.setAttribute(
      "aria-label",
      `${on ? "Remove" : "Save"} ${state.cards.get(n.dataset.key)?.title || "home"}`,
    );
    control.textContent = on ? "♥" : "♡";
  });
  renderSelection();
}
async function toggle(c) {
  if (state.busy) return;
  const listingKey = c.listing_key;
  if (state.favoritePending.has(listingKey)) return;
  const saved = state.selected.has(listingKey);
  if (!saved) {
    if (state.selected.size === 6) {
      showError("Save up to six homes in a conversation.");
      return;
    }
  }
  if (state.sample) {
    if (saved) state.selected.delete(listingKey);
    else state.selected.set(listingKey, c);
    syncSelection();
    return;
  }
  state.favoritePending.add(listingKey);
  syncSelection();
  try {
    await api(`/api/favorites/${saved ? "remove" : "add"}`, {
      listing_key: listingKey,
    });
    if (saved) state.selected.delete(listingKey);
    else state.selected.set(listingKey, c);
    const conversation = state.conversations.find(
      (item) => item.conversation_id === state.conversationId,
    );
    if (conversation) {
      conversation.favorite_count = state.selected.size;
      renderConversationList();
    }
    $("#error").hidden = true;
  } catch (e) {
    showError(e.message);
  } finally {
    state.favoritePending.delete(listingKey);
    syncSelection();
  }
}
function requirement(parent, data) {
  const p = data.profile || {};
  const box = el("section", "confirmation glass");
  box.append(el("h2", "confirmation-title", "A place that fits your life."));
  const chips = el("div", "chips");
  const chip = (label, text) => {
    const n = el("div", "chip");
    n.append(el("small", "", label), document.createTextNode(text));
    chips.append(n);
  };
  if (p.intent) chip("Looking to", friendly(p.intent));
  for (const c of p.listing_constraints || []) {
    const op =
      {
        lte: "Up to ",
        gte: "At least ",
        lt: "Below ",
        gt: "Above ",
        neq: "Not ",
        between: "",
      }[c.operator] || "";
    chip(
      labels[c.field_path] || friendly(c.field_path.split(".").at(-1)),
      op + friendly(c.value),
    );
  }
  for (const c of p.derived_data_requirements || [])
    chip(
      friendly(c.category),
      [c.target, c.metric?.replaceAll("_", " "), c.value != null ? `${c.operator === "lte" ? "within " : ""}${c.value}${c.metric === "travel_time" ? " min" : c.unit ? " " + c.unit : ""}` : null]
        .filter((x) => x != null)
        .map(value)
        .join(" · "),
    );
  for (const c of p.open_data_requirements || [])
    chip("Preference", c.description);
  if (!chips.childNodes.length)
    chip("Your requirements", data.confirmation.summary);
  box.append(chips);
  const actions = el("div", "confirmation-actions");
  actions.append(
    button("Looks good, find my home ↗", "primary", async () => {
      if (state.sample) {
        actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
        sampleResults();
        return;
      }
      await send("Confirm my requirements", {
        confirmation_id: data.confirmation.confirmation_id,
      });
    }),
    button("Make a change", "secondary", () => {
      $("#input").value = "I’d like to change ";
      $("#input").focus();
    }),
  );
  box.append(actions);
  parent.append(box);
}
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
const clarificationChoices = {
  intent: [
    ["Rent", "I am looking to rent."],
    ["Buy", "I am looking to buy."],
  ],
  "listing_constraints.attributes.listing_scope": [
    ["Whole unit", "I am looking for a whole unit."],
    ["Private room", "I am looking for a private room."],
    ["Shared bedspace", "I am looking for a shared bedspace."],
  ],
  "listing_constraints.price.period": [
    ["Per month", "My rental budget is per month."],
    ["Per week", "My rental budget is per week."],
  ],
  "listing_constraints.price.currency": [
    ["SGD", "My budget is in SGD."],
    ["USD", "My budget is in USD."],
    ["MYR", "My budget is in MYR."],
    ["CNY", "My budget is in CNY."],
  ],
};
function clarification(parent, data) {
  const questions = data.clarification_questions || [];
  if (!questions.length) return;
  const controls = el("div", "clarification-controls");
  const responders = [];
  let submit;
  const updateSubmit = () => {
    submit.disabled = !responders.some((responder) => responder.answer());
  };
  const submitOnEnter = (event) => {
    if (event.key === "Enter" && !event.isComposing && !submit.disabled) {
      event.preventDefault();
      submit.click();
    }
  };
  questions.forEach((question) => {
    const card = el("section", "clarification-card");
    card.append(el("h3", "", question.text));
    const choices = clarificationChoices[question.field];
    if (choices) {
      const options = el("div", "clarification-options");
      let selectedAnswer = "";
      const optionButtons = [];
      choices.forEach(([label, answer]) => {
        const option = button(label, "clarification-option", () => {
          selectedAnswer = answer;
          optionButtons.forEach((item) => {
            const selected = item === option;
            item.classList.toggle("selected", selected);
            item.setAttribute("aria-pressed", String(selected));
          });
          updateSubmit();
        });
        option.setAttribute("aria-pressed", "false");
        optionButtons.push(option);
        options.append(option);
      });
      responders.push({ answer: () => selectedAnswer, validate: () => true });
      card.append(options);
    } else if (question.field === "listing_constraints.price.amount") {
      const fields = el("div", "clarification-form budget-form");
      const minimum = document.createElement("input");
      minimum.type = "number";
      minimum.min = "0";
      minimum.step = "100";
      minimum.inputMode = "numeric";
      minimum.placeholder = "Min SGD";
      minimum.setAttribute("aria-label", "Minimum budget in SGD");
      const maximum = document.createElement("input");
      maximum.type = "number";
      maximum.min = "0";
      maximum.step = "100";
      maximum.inputMode = "numeric";
      maximum.placeholder = "Max SGD";
      maximum.setAttribute("aria-label", "Maximum budget in SGD");
      const budgetAnswer = () => {
        const min = minimum.valueAsNumber;
        const max = maximum.valueAsNumber;
        return Number.isFinite(min) && Number.isFinite(max)
          ? `My budget is SGD ${min} to SGD ${max}.`
          : Number.isFinite(max)
            ? `My budget is up to SGD ${max}.`
            : Number.isFinite(min)
              ? `My minimum budget is SGD ${min}.`
              : "";
      };
      responders.push({
        answer: budgetAnswer,
        validate: () => {
          const min = minimum.valueAsNumber;
          const max = maximum.valueAsNumber;
          maximum.setCustomValidity("");
          if (Number.isFinite(min) && Number.isFinite(max) && min > max) {
            maximum.setCustomValidity(
              "Maximum budget must be at least the minimum.",
            );
            maximum.reportValidity();
            return false;
          }
          return true;
        },
      });
      for (const input of [minimum, maximum]) {
        input.oninput = () => {
          maximum.setCustomValidity("");
          updateSubmit();
        };
        input.onkeydown = submitOnEnter;
      }
      fields.append(minimum, maximum);
      card.append(fields);
    } else {
      const fields = el("div", "clarification-form");
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 6000;
      input.placeholder = "Type your answer";
      input.setAttribute("aria-label", question.text);
      const textAnswer = () => {
        const answer = input.value.trim();
        if (!answer) return "";
        return question.field === "derived_data_requirements.location"
          ? `I would like to live near or commute to ${answer}.`
          : `${question.text} ${answer}`;
      };
      responders.push({ answer: textAnswer, validate: () => true });
      input.oninput = updateSubmit;
      input.onkeydown = submitOnEnter;
      fields.append(input);
      card.append(fields);
    }
    controls.append(card);
  });
  const submitRow = el("div", "clarification-submit");
  submitRow.append(
    el("span", "", "Answer one or more details, then continue."),
  );
  submit = button("Continue with these details →", "primary", () => {
    if (!responders.every((responder) => responder.validate())) return;
    const answers = responders
      .map((responder) => responder.answer())
      .filter(Boolean);
    if (answers.length) send(answers.join("\n"));
  });
  submit.disabled = true;
  submitRow.append(submit);
  controls.append(submitRow);
  parent.append(controls);
}
function render(data) {
  const hasClarification = Boolean(
    !data.confirmation && data.clarification_questions?.length,
  );
  const text = data.confirmation
    ? ""
    : hasClarification
      ? "A few quick details will help me narrow down your search."
      : data.recommendation?.summary || data.assistant_response;
  const n = message("assistant", text);
  if (data.confirmation) requirement(n, data);
  if (hasClarification) clarification(n, data);
  if (data.recommendation) results(n, data);
  scrollEnd();
}
async function send(text, extra = {}) {
  if (state.busy || !text.trim()) return;
  if (state.sample) {
    showError(
      "This is a sample conversation. Start a new conversation to use the connected service.",
    );
    return;
  }
  if (!state.session) {
    showError("Connection unavailable. Start a new conversation to reconnect.");
    return;
  }
  $("#error").hidden = true;
  const payload = {
    text: text.trim(),
    selected_listing_keys: [...state.selected.keys()],
    ...extra,
  };
  const fingerprint = JSON.stringify(payload);
  const id =
    state.retry?.fingerprint === fingerprint
      ? state.retry.id
      : crypto.randomUUID();
  if (state.retry?.id !== id)
    message(
      "user",
      text +
        (state.selected.size
          ? "\n↳ " + [...state.selected.values()].map((c) => c.title).join(", ")
          : ""),
    );
  state.retry = { fingerprint, id };
  busy(true);
  const controller = new AbortController();
  const active = { id, controller, stopped: false };
  state.active = active;
  const pending = el("div", "pending");
  const hint = el("span", "pending-hint");
  const node = el("small", "pending-node", "Starting your search…");
  const elapsed = el("small", "pending-elapsed");
  pending.append(hint, node, elapsed);
  const hints = extra.confirmation_id ? [
    "Looking for a place that feels like home in Singapore…",
    "Searching from the heartlands to the city fringe…",
    "Finding room for family dinners, kopi mornings, and quiet nights…",
    "Checking MRT connections and the neighbourhood around each home…",
    "Balancing commute time, everyday life, and your budget…",
    "Good homes take a little checking — you can stop the search at any time.",
  ] : [
    "Putting your Singapore home wish list together…",
    "Thinking about space, commute, and neighbourhood life…",
    "Making room for what matters to you and your family…",
    "A clearer brief brings you closer to the right home…",
    "You can stop this request at any time.",
  ];
  const started = Date.now();
  let lastHint = -1;
  const tick = () => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    const index = Math.floor(seconds / 5) % hints.length;
    if (index !== lastHint) {
      hint.textContent = hints[index];
      hint.getAnimations?.().forEach((animation) => animation.cancel());
      if (!matchMedia("(prefers-reduced-motion: reduce)").matches)
        hint.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "translateY(0)" }], { duration: 350 });
      lastHint = index;
    }
    elapsed.textContent = `${seconds}s elapsed`;
  };
  tick();
  const progressTimer = setInterval(tick, 1000);
  let progressRequestActive = false;
  const updateNode = async () => {
    if (progressRequestActive || state.active !== active) return;
    progressRequestActive = true;
    try {
      const progress = await api("/api/progress", { message_id: id });
      if (state.active !== active) return;
      if (progress.label) node.textContent = progress.label;
    } catch {}
    finally {
      progressRequestActive = false;
    }
  };
  updateNode();
  const nodeTimer = setInterval(updateNode, 1000);
  const deadlineTimer = setTimeout(() => {
    active.timedOut = true;
    controller.abort();
    api("/api/cancel", { message_id: id }).catch(() => {});
  }, 345000);
  $("#messages").append(pending);
  scrollEnd();
  try {
    const data = await api("/api/turn", { ...payload, message_id: id }, controller.signal);
    if (active.stopped) return;
    state.retry = null;
    $("#input").value = "";
    state.selected.clear();
    syncSelection();
    document
      .querySelectorAll(".confirmation-actions")
      .forEach((n) => n.remove());
    document
      .querySelectorAll(".clarification-controls")
      .forEach((n) => n.remove());
    render(data);
    refreshConversations().catch(() => {});
  } catch (e) {
    if (active.stopped) {
      state.retry = null;
      message("assistant", "Search stopped. You can update your requirements or search again.");
    } else if (active.timedOut) {
      state.retry = null;
      showError("This request timed out. A stop request was sent to the server. Please retry or start a new conversation.");
    } else showError(e.message);
  } finally {
    clearInterval(progressTimer);
    clearInterval(nodeTimer);
    clearTimeout(deadlineTimer);
    state.active = null;
    pending.remove();
    busy(false);
  }
}
$("#stop").onclick = async () => {
  const active = state.active;
  if (!active) return;
  $("#stop").disabled = true;
  $("#stop").textContent = "Stopping…";
  try {
    const result = await api("/api/cancel", { message_id: active.id });
    if (state.active !== active) return;
    if (result.status === "cancelled") {
      active.stopped = true;
      active.controller.abort();
    }
  } catch (e) {
    if (state.active !== active) return;
    showError("Could not confirm the stop. Please try Stop again.");
    $("#stop").disabled = false;
    $("#stop").textContent = "■ Stop";
  }
};
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
$("#preview").onclick = () => {
  state.sample = true;
  $("#welcome").hidden = true;
  $("#messages").append(
    el(
      "p",
      "sample-notice",
      "Design preview · Illustrative homes, not live search results.",
    ),
  );
  message("user", "A two-bedroom home near NUS, up to S$3,500 a month.");
  render({
    confirmation: { confirmation_id: "sample", summary: "" },
    profile: {
      intent: "rent",
      listing_constraints: [
        { field_path: "price.amount", operator: "lte", value: 3500 },
        { field_path: "price.currency", value: "SGD" },
        { field_path: "bedrooms", value: 2 },
        { field_path: "attributes.listing_scope", value: "whole_unit" },
      ],
      derived_data_requirements: [
        { category: "Location", target: "NUS", metric: "nearby" },
      ],
    },
  });
};
$("#new-chat").onclick = async () => {
  if (state.busy) return;
  state.session = null;
  state.conversationId = null;
  resetConversationView();
  await init(null);
};
$("#composer").onsubmit = (e) => {
  e.preventDefault();
  send($("#input").value);
};
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send($("#input").value);
  }
});
setupSidebarResize();
bootstrap();

$("#mobile-new").onclick = () => $("#new-chat").click();
