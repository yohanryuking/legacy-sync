/* Dashboard sin build step: fetch para el estado inicial, WebSocket para
 * saber cuándo volver a pedirlo. Ver docs/DECISIONS.md ADR-009 para el
 * porqué de "invalidar y refetchear" en vez de reconciliar eventos.
 */

const SUMMARY_LABELS = {
  total_legacy: "Leídos del legado",
  migrated: "Migrados",
  validation_failed: "Inválidos",
  retry_pending: "En cola (pendiente)",
  retry_failed_permanent: "Fallidos definitivos",
  retry_succeeded: "Recuperados por reintento",
};

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function renderSummary(summary) {
  const el = document.getElementById("summary");
  el.innerHTML = "";
  for (const [key, label] of Object.entries(SUMMARY_LABELS)) {
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<div class="value">${summary[key]}</div><div class="label">${label}</div>`;
    el.appendChild(card);
  }
}

function renderRetryQueue(items) {
  const tbody = document.querySelector("#retry-table tbody");
  tbody.innerHTML = "";
  for (const item of items) {
    const tr = document.createElement("tr");
    const canRetry = item.status !== "succeeded";
    tr.innerHTML = `
      <td>${item.legacy_id}</td>
      <td>${item.natural_key}</td>
      <td class="status-${item.status}">${item.status}</td>
      <td>${item.attempt_count}/${item.max_attempts}</td>
      <td>${item.last_error ?? ""}</td>
      <td><button data-id="${item.id}" ${canRetry ? "" : "disabled"}>Reintentar ahora</button></td>
    `;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll("button[data-id]").forEach((btn) => {
    btn.addEventListener("click", () => forceRetry(btn.dataset.id));
  });
}

function renderValidationFailures(items) {
  const tbody = document.querySelector("#validation-table tbody");
  tbody.innerHTML = "";
  for (const item of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.legacy_id ?? ""}</td>
      <td>${item.error_field ?? ""}</td>
      <td>${item.error_message ?? ""}</td>
      <td>${new Date(item.created_at).toLocaleString()}</td>
    `;
    tbody.appendChild(tr);
  }
}

async function forceRetry(itemId) {
  await fetchJSON(`/retry-queue/${itemId}/retry`, { method: "POST" });
  await refreshAll();
}

async function refreshAll() {
  const [summary, retryQueue, validationFailures] = await Promise.all([
    fetchJSON("/migrations/summary"),
    fetchJSON("/retry-queue?limit=50"),
    fetchJSON("/migrations/validation-failures?limit=50"),
  ]);
  renderSummary(summary);
  renderRetryQueue(retryQueue);
  renderValidationFailures(validationFailures);
}

function connectLiveUpdates() {
  const statusEl = document.getElementById("conn-status");
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${location.host}/migrations/live`);

  ws.onopen = () => {
    statusEl.textContent = "en vivo";
    statusEl.className = "badge badge-on";
  };
  ws.onclose = () => {
    statusEl.textContent = "desconectado";
    statusEl.className = "badge badge-off";
    setTimeout(connectLiveUpdates, 3000);
  };

  // Cualquier NOTIFY de Postgres llega aca -- no importa el contenido
  // exacto, alcanza con re-pedir el estado (debounced para no saturar si
  // llegan varios avisos juntos, ej. un batch grande corriendo).
  let debounceTimer = null;
  ws.onmessage = () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(refreshAll, 250);
  };
}

refreshAll();
connectLiveUpdates();
