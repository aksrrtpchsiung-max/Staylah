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
