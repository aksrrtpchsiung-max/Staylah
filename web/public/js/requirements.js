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
