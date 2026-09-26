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
