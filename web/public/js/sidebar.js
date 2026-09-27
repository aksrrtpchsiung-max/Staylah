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
