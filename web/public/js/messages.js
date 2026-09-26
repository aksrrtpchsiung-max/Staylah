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
