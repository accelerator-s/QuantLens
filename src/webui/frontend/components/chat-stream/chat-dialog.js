import { el } from "../../core/dom.js";

function bindHistoryResize(dialog, historyHost, divider, backdrop) {
  let width = 228, drag = null;
  const desktop = window.matchMedia("(min-width: 761px)");

  function update(next = width) {
    if (!desktop.matches) { stopDrag(); return; }
    const available = dialog.clientWidth - divider.offsetWidth;
    const maximum = Math.max(180, Math.floor(Math.min(available * 0.45, available - 320)));
    width = Math.round(Math.max(180, Math.min(maximum, next)));
    dialog.style.setProperty("--chat-history-width", `${width}px`);
    divider.setAttribute("aria-valuemin", "180");
    divider.setAttribute("aria-valuemax", String(maximum));
    divider.setAttribute("aria-valuenow", String(width));
    divider.setAttribute("aria-valuetext", `对话历史宽度 ${width} 像素`);
  }
  function stopDrag() {
    const pointerId = drag?.pointerId;
    drag = null;
    backdrop.classList.remove("is-resizing");
    if (pointerId !== undefined && divider.hasPointerCapture(pointerId)) divider.releasePointerCapture(pointerId);
  }
  divider.addEventListener("pointerdown", event => {
    if (event.button !== 0 || drag || !desktop.matches) return;
    event.preventDefault();
    divider.focus({ preventScroll: true });
    drag = { pointerId: event.pointerId, x: event.clientX, width: historyHost.getBoundingClientRect().width };
    divider.setPointerCapture(event.pointerId);
    backdrop.classList.add("is-resizing");
  });
  divider.addEventListener("pointermove", event => {
    if (drag?.pointerId === event.pointerId) update(drag.width + event.clientX - drag.x);
  });
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
    divider.addEventListener(type, event => { if (drag?.pointerId === event.pointerId) stopDrag(); });
  }
  divider.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? 40 : 10;
    update(event.key === "Home" ? 180 : event.key === "End" ? dialog.clientWidth : width + (event.key === "ArrowLeft" ? -step : step));
  });
  const observer = new ResizeObserver(() => update());
  observer.observe(dialog);
  update();
  return () => { stopDrag(); observer.disconnect(); };
}

// Shared shell: only the conversation scrolls; the composer and actions stay in view.
export function createChatDialog({ title, description = "", history = false, onClose } = {}) {
  const previousFocus = document.activeElement;
  const backdrop = el("div", "dialog-backdrop chat-dialog-backdrop");
  const dialog = el("section", "dialog chat-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", title);
  const header = el("header", "chat-dialog__header");
  const heading = el("div", "chat-dialog__heading");
  heading.append(el("h2", "chat-dialog__title", title));
  if (description) heading.append(el("p", "chat-dialog__description", description));
  const closeButton = el("button", "btn btn--ghost chat-dialog__close");
  closeButton.innerHTML = '<span class="chat-dialog__close-mark" aria-hidden="true"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="m5 5 10 10M15 5 5 15"/></svg></span>';
  closeButton.type = "button";
  closeButton.setAttribute("aria-label", "关闭对话");
  header.append(heading);
  const historyHost = el("aside", "chat-history");
  historyHost.setAttribute("aria-label", "对话历史");
  const historyToggle = el("button", "btn btn--ghost chat-dialog__history-toggle", "历史");
  historyToggle.type = "button";
  historyToggle.setAttribute("aria-expanded", "false");
  historyToggle.addEventListener("click", () => {
    const open = dialog.classList.toggle("is-history-open");
    historyToggle.setAttribute("aria-expanded", String(open));
  });
  if (history) header.append(historyToggle);
  header.append(closeButton);
  const toolbar = el("div", "chat-dialog__toolbar");
  const chatHost = el("div");
  const actions = el("footer", "chat-dialog__actions");
  const main = el("div", "chat-dialog__main");
  const divider = el("div", "chat-dialog__divider");
  divider.tabIndex = 0;
  divider.setAttribute("role", "separator");
  divider.setAttribute("aria-orientation", "vertical");
  divider.setAttribute("aria-label", "调整对话历史与聊天区域宽度");
  divider.title = "左右拖动调整宽度，也可使用左右方向键";
  main.append(header, toolbar, chatHost, actions);
  if (history) {
    dialog.classList.add("chat-dialog--history");
    dialog.append(historyHost, divider);
  }
  dialog.append(main);
  backdrop.append(dialog);
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  document.body.append(backdrop);
  const disposeResize = history ? bindHistoryResize(dialog, historyHost, divider, backdrop) : null;

  // visualViewport accounts for mobile keyboards as well as browser chrome.
  const viewport = window.visualViewport;
  function fitViewport() {
    backdrop.style.height = `${viewport?.height || window.innerHeight}px`;
    backdrop.style.top = `${viewport?.offsetTop || 0}px`;
  }
  fitViewport();
  viewport?.addEventListener("resize", fitViewport);
  viewport?.addEventListener("scroll", fitViewport);
  window.addEventListener("resize", fitViewport);
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    disposeResize?.();
    onClose?.();
    viewport?.removeEventListener("resize", fitViewport);
    viewport?.removeEventListener("scroll", fitViewport);
    window.removeEventListener("resize", fitViewport);
    backdrop.remove();
    document.body.style.overflow = previousOverflow;
    if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
  }
  closeButton.addEventListener("click", close);
  backdrop.addEventListener("click", event => { if (event.target === backdrop) close(); });
  dialog.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (dialog.querySelector(".select.is-open")) return;
      event.preventDefault();
      close();
    }
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll("button, textarea, input, [tabindex='0']")]
      .filter(item => !item.disabled && item.getClientRects().length);
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  });
  closeButton.focus({ preventScroll: true });
  return { dialog, toolbar, chatHost, historyHost, actions, close };
}
