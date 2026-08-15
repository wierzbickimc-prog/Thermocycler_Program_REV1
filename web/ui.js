/* Brand-styled modal dialogs (confirm / prompt), replacing window.confirm. */

function shell(build) {
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-back";
    const modal = document.createElement("div");
    modal.className = "modal";
    back.appendChild(modal);
    document.body.appendChild(back);

    let settled = false;
    const close = value => {
      if (settled) return;
      settled = true;
      back.classList.remove("show");
      setTimeout(() => back.remove(), 180);
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = ev => {
      if (ev.key === "Escape") close(null);
      if (ev.key === "Enter" && modal.dataset.enter === "1") {
        ev.preventDefault();
        modal.querySelector("[data-ok]").click();
      }
    };
    document.addEventListener("keydown", onKey);
    back.addEventListener("mousedown", ev => { if (ev.target === back) close(null); });

    build(modal, close);
    requestAnimationFrame(() => back.classList.add("show"));
  });
}

/** Confirm dialog. Resolves true / null. `points` renders a bullet list. */
export function confirmDialog({ title, body, points = [], ok = "Continue",
                                cancel = "Cancel", danger = false }) {
  return shell((modal, close) => {
    modal.dataset.enter = "1";
    modal.innerHTML = `
      <h3></h3>
      <p></p>
      ${points.length ? `<ul class="warn-list">${points.map(p => `<li>${p}</li>`).join("")}</ul>` : ""}
      <div class="modal-actions">
        <button class="btn" data-cancel></button>
        <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-ok></button>
      </div>`;
    modal.querySelector("h3").textContent = title;
    modal.querySelector("p").textContent = body;
    modal.querySelector("[data-ok]").textContent = ok;
    modal.querySelector("[data-cancel]").textContent = cancel;
    modal.querySelector("[data-ok]").onclick = () => close(true);
    modal.querySelector("[data-cancel]").onclick = () => close(null);
    requestAnimationFrame(() => modal.querySelector("[data-ok]").focus());
  });
}

/** Text prompt. Resolves the trimmed string, or null if cancelled/empty. */
export function promptDialog({ title, body = "", value = "", ok = "Save",
                               placeholder = "" }) {
  return shell((modal, close) => {
    modal.dataset.enter = "1";
    modal.innerHTML = `
      <h3></h3>
      ${body ? "<p></p>" : ""}
      <input type="text">
      <div class="modal-actions">
        <button class="btn" data-cancel>Cancel</button>
        <button class="btn btn-primary" data-ok></button>
      </div>`;
    modal.querySelector("h3").textContent = title;
    if (body) modal.querySelector("p").textContent = body;
    const input = modal.querySelector("input");
    input.value = value;
    input.placeholder = placeholder;
    modal.querySelector("[data-ok]").textContent = ok;
    modal.querySelector("[data-ok]").onclick = () => close(input.value.trim() || null);
    modal.querySelector("[data-cancel]").onclick = () => close(null);
    requestAnimationFrame(() => { input.focus(); input.select(); });
  });
}
