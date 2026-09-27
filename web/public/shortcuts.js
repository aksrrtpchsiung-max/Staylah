// Preserve user-written content, combining the three house-hunting criteria in the order they were added.
(() => {
  const input = document.querySelector("#input");
  const choices = [...document.querySelectorAll(".quick-choice")];
  let lastTrigger = null;
  function closePanels() {
    choices.forEach((choice) => {
      choice.querySelector(".quick-panel").hidden = true;
      choice
        .querySelector(".quick-trigger")
        .setAttribute("aria-expanded", "false");
    });
  }
  function openPanel(choice) {
    if (input.disabled) return;
    closePanels();
    choice.querySelector(".quick-panel").hidden = false;
    lastTrigger = choice.querySelector(".quick-trigger");
    lastTrigger.setAttribute("aria-expanded", "true");
  }
  choices.forEach((choice) => {
    const trigger = choice.querySelector(".quick-trigger");
    trigger.addEventListener("click", () => {
      if (trigger.getAttribute("aria-expanded") === "true") closePanels();
      else openPanel(choice);
    });
    choice.addEventListener("focusout", () => {
      setTimeout(() => {
        if (
          !choice.contains(document.activeElement)
        )
          closePanels();
      }, 0);
    });
  });
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest(".quick-choice")) closePanels();
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.key === "Escape" &&
      choices.some((c) => !c.querySelector(".quick-panel").hidden)
    ) {
      closePanels();
      lastTrigger?.focus();
    }
  });
  function appendCondition(text, form) {
    if (input.disabled) return;
    const existing = input.value;
    const next =
      existing + (existing && !/\s$/.test(existing) ? "\n" : "") + text;
    if (next.length > input.maxLength) {
      input.setCustomValidity("Your message is too long. Please shorten it.");
      input.reportValidity();
      input.setCustomValidity("");
      return;
    }
    input.value = next;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const trigger = form.parentElement.querySelector(".quick-trigger");
    trigger.classList.add("has-condition");
    trigger.querySelector("b").textContent = "✓";
    document.querySelector("#condition-status").textContent = "Added: " + text;
    closePanels();
    input.focus();
    input.setSelectionRange(next.length, next.length);
    input.scrollTop = input.scrollHeight;
  }
  const time = document.querySelector("#commute-time");
  for (let minutes = 10; minutes <= 60; minutes += 5) {
    const option = new Option(`Within ${minutes} minutes`, String(minutes));
    option.selected = minutes === 30;
    time.add(option);
  }
  document
    .querySelector("#commute-panel")
    .addEventListener("submit", (event) => {
      event.preventDefault();
      const field = document.querySelector("#commute-place");
      const place = field.value.trim();
      if (!place) {
        field.value = "";
        field.reportValidity();
        return;
      }
      const mode =
        document.querySelector("#commute-mode").selectedOptions[0].textContent;
      appendCondition(
        `Commute: within ${time.value} minutes one way to ${place} by ${mode.toLowerCase()}.`,
        event.currentTarget,
      );
    });
  document
    .querySelector("#school-panel")
    .addEventListener("submit", (event) => {
      event.preventDefault();
      const field = document.querySelector("#school-name");
      const school = field.value.trim();
      if (!school) {
        field.value = "";
        field.reportValidity();
        return;
      }
      appendCondition(
        `School: find a home near ${school}.`,
        event.currentTarget,
      );
    });
  let railLines = [];
  const line = document.querySelector("#mrt-line");
  const station = document.querySelector("#mrt-station");
  const railButton = document.querySelector("#mrt-panel .add-condition");
  railButton.disabled = true;
  function renderStations() {
    station.replaceChildren();
    const current = railLines.find((item) => item.id === line.value);
    current?.stations.forEach((item) =>
      station.add(new Option(`${item.code} · ${item.name}`, item.code)),
    );
    station.selectedIndex = 0;
  }
  line.addEventListener("change", renderStations);
  fetch("/mrt-stations.json")
    .then((response) => {
      if (!response.ok) throw Error();
      return response.json();
    })
    .then((data) => {
      railLines = data.lines;
      railLines.forEach((item) => line.add(new Option(item.name, item.id)));
      line.value = "EWL";
      renderStations();
      railButton.disabled = false;
    })
    .catch(() => {
      const error = document.querySelector("#mrt-panel .shortcut-error");
      error.textContent = "Station list unavailable. Please refresh and try again.";
      error.hidden = false;
    });
  document.querySelector("#mrt-panel").addEventListener("submit", (event) => {
    event.preventDefault();
    const current = railLines.find((item) => item.id === line.value);
    const chosen = current?.stations.find(
      (item) => item.code === station.value,
    );
    if (!chosen) return;
    const distance = document.querySelector("#mrt-distance").value;
    appendCondition(
      `MRT: find a home within ${distance} metres of ${chosen.name} MRT (${chosen.code}), ${current.name} line.`,
      event.currentTarget,
    );
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(130, input.scrollHeight) + "px";
  });
  document.querySelector("#new-chat").addEventListener("click", () => {
    closePanels();
    choices.forEach((choice) => {
      choice.querySelector("form").reset();
      const trigger = choice.querySelector(".quick-trigger");
      trigger.classList.remove("has-condition");
      trigger.querySelector("b").textContent = "＋";
    });
    line.value = "EWL";
    renderStations();
    input.style.height = "auto";
  });
})();
