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
