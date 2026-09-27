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
