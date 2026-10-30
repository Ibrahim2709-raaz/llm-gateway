"use strict";

const state = {
  account: null,
  organization: null,
  csrf: sessionStorage.getItem("llm_gateway_csrf") || "",
  period: new Date().toISOString().slice(0, 7),
  usage: null,
  preference: "balanced",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));
const formatMoney = (value) => value === null || value === undefined
  ? "—"
  : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 6 }).format(value);
const formatNumber = (value) => new Intl.NumberFormat("en-US").format(value || 0);
const formatDate = (value) => new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
const titleCase = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase()) && state.csrf) {
    headers["X-CSRF-Token"] = state.csrf;
  }
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  const payload = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
    error.code = payload?.error?.code;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function toast(message, error = false) {
  const item = document.createElement("div");
  item.className = `toast${error ? " error" : ""}`;
  item.textContent = message;
  $("#toast-region").append(item);
  window.setTimeout(() => item.remove(), 5000);
}

function setBusy(form, busy) {
  const button = $("button[type='submit']", form);
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = "Working…";
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
  }
  button.disabled = busy;
}

function showAuthPanel(name) {
  $$('[data-auth-panel], #verify-form, #reset-form').forEach((panel) => panel.classList.add("hidden"));
  const panel = $(`[data-auth-panel="${name}"]`) || $(`#${name}-form`);
  panel?.classList.remove("hidden");
  $$(".auth-tab").forEach((tab) => {
    const active = tab.dataset.authTab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function showAuthenticated(account) {
  state.account = account;
  state.organization = account.organizations.find((item) => item.id === account.active_organization_id);
  $("#auth-view").classList.add("hidden");
  $("#app-view").classList.remove("hidden");
  $("#user-name").textContent = account.display_name;
  $("#welcome-name").textContent = account.display_name.split(" ")[0];
  $("#period-chip").textContent = new Date(`${state.period}-01T00:00:00`).toLocaleDateString("en-US", { month: "long", year: "numeric" });
  renderOrganizationSwitcher();
  applyRoleVisibility();
}

function renderOrganizationSwitcher() {
  const select = $("#organization-select");
  select.replaceChildren(...state.account.organizations.map((organization) => {
    const option = document.createElement("option");
    option.value = organization.id;
    option.textContent = organization.name;
    option.selected = organization.id === state.account.active_organization_id;
    return option;
  }));
  $("#org-avatar").textContent = state.organization?.name?.[0]?.toUpperCase() || "O";
  $("#role-pill").textContent = titleCase(state.organization?.role);
}

function applyRoleVisibility() {
  const isOwner = state.organization?.role === "owner";
  $$(".owner-only").forEach((element) => element.classList.toggle("hidden", !isOwner));
  $('[data-view="audit"]').classList.toggle("hidden", !isOwner);
  if (!isOwner && $('[data-page="audit"]:not(.hidden)')) showPage("overview");
}

async function loadAccount() {
  try {
    const account = await api("/v1/portal/auth/me");
    showAuthenticated(account);
    await loadOverview();
    return true;
  } catch (error) {
    if (error.status !== 401) toast(error.message, true);
    $("#auth-view").classList.remove("hidden");
    $("#app-view").classList.add("hidden");
    return false;
  }
}

async function loadOverview() {
  if (!state.organization) return;
  const organizationId = state.organization.id;
  const [usage, preference] = await Promise.all([
    api(`/v1/portal/organizations/${organizationId}/usage?period=${state.period}`),
    api(`/v1/portal/organizations/${organizationId}/routing/preference`),
  ]);
  state.usage = usage;
  state.preference = preference.strategy;
  renderUsage(usage);
  renderPreference(preference.strategy);
}

function renderUsage(usage) {
  $("#metric-cost").textContent = formatMoney(usage.cost_usd);
  $("#metric-requests").textContent = formatNumber(usage.request_count);
  $("#metric-tokens").textContent = formatNumber(usage.total_tokens);
  $("#metric-remaining").textContent = formatMoney(usage.remaining_budget_usd);
  $("#budget-caption").textContent = usage.budget_usd === null ? "No budget configured" : `of ${formatMoney(usage.budget_usd)} monthly`;
  const percentage = usage.budget_usd ? Math.min(100, (usage.cost_usd / usage.budget_usd) * 100) : 0;
  $("#budget-bar-fill").style.width = `${percentage}%`;
  $("#budget-used-label").textContent = `${formatMoney(usage.cost_usd)} used`;
  $("#budget-limit-label").textContent = usage.budget_usd === null ? "No limit" : `${formatMoney(usage.budget_usd)} limit`;
  const badge = $("#budget-state");
  badge.className = "status-badge neutral";
  if (usage.budget_usd === null) badge.textContent = "Not configured";
  else if (percentage >= 100) { badge.textContent = "Limit reached"; badge.className = "status-badge danger"; }
  else if (percentage >= 80) { badge.textContent = "Review spend"; badge.className = "status-badge warning"; }
  else { badge.textContent = "Within budget"; badge.className = "status-badge success"; }
  $("#usage-table").innerHTML = `<tr><td>${escapeHtml(usage.period)}</td><td>${formatNumber(usage.request_count)}</td><td>${formatNumber(usage.prompt_tokens)}</td><td>${formatNumber(usage.completion_tokens)}</td><td>${formatNumber(usage.total_tokens)}</td><td>${formatMoney(usage.cost_usd)}</td><td>${formatMoney(usage.remaining_budget_usd)}</td></tr>`;
  if (usage.budget_usd !== null) $("#budget-form [name='limit_usd']").value = usage.budget_usd;
}

function renderPreference(strategy) {
  const labels = { balanced: "Balanced", cost: "Lowest cost", latency: "Lowest latency", quality: "Highest quality" };
  $("#overview-strategy").textContent = labels[strategy] || titleCase(strategy);
  const input = $(`#routing-preference-form [value="${strategy}"]`);
  if (input) input.checked = true;
}

function showPage(name) {
  $$("[data-page]").forEach((page) => page.classList.toggle("hidden", page.dataset.page !== name));
  $$("[data-view]").forEach((item) => item.classList.toggle("is-active", item.dataset.view === name));
  $(".sidebar").classList.remove("is-open");
  $("#main-content").focus();
  loadPage(name).catch((error) => toast(error.message, true));
}

async function loadPage(name) {
  if (["overview", "usage"].includes(name)) return loadOverview();
  if (name === "keys") return loadKeys();
  if (name === "routing") return loadRouting();
  if (name === "team") return loadMembers();
  if (name === "audit") return loadAudit();
}

async function loadKeys() {
  const payload = await api(`/v1/portal/organizations/${state.organization.id}/api-keys`);
  $("#keys-table").innerHTML = payload.data.length ? payload.data.map((key) => `<tr><td><strong>${escapeHtml(key.name)}</strong></td><td><code>${escapeHtml(key.key_prefix)}…</code></td><td>${formatDate(key.created_at)}</td><td><span class="status-badge ${key.revoked_at ? "neutral" : "success"}">${key.revoked_at ? "Revoked" : "Active"}</span></td><td>${key.revoked_at ? "" : `<button class="text-button revoke-key" data-key-id="${escapeHtml(key.id)}">Revoke</button>`}</td></tr>`).join("") : '<tr><td colspan="5" class="empty-state">No API keys yet.</td></tr>';
}

async function loadRouting() {
  const organizationId = state.organization.id;
  const [preference, decisions] = await Promise.all([
    api(`/v1/portal/organizations/${organizationId}/routing/preference`),
    api(`/v1/portal/organizations/${organizationId}/routing/decisions?limit=50`),
  ]);
  state.preference = preference.strategy;
  renderPreference(preference.strategy);
  $("#routing-decisions").innerHTML = decisions.data.length ? decisions.data.map((decision) => `<article class="decision-item"><div><p><strong>${escapeHtml(decision.selected_provider)} / ${escapeHtml(decision.selected_model)}</strong></p><p class="muted">${titleCase(decision.task_category)} · ${titleCase(decision.difficulty)} · ${Math.round(Number(decision.classification_confidence) * 100)}% confidence</p><ul class="reason-list">${decision.reason_codes.map((reason) => `<li>${escapeHtml(titleCase(reason))}</li>`).join("") || "<li>Static profile</li>"}</ul></div><div class="decision-meta"><div>${formatDate(decision.created_at)}</div><div>${escapeHtml(decision.policy_version)}</div><div>${escapeHtml(decision.score_version)}</div></div></article>`).join("") : '<p class="empty-state">No routing decisions yet. Make a chat request to create one.</p>';
}

async function loadMembers() {
  if (state.organization.role !== "owner") {
    $("#members-table").innerHTML = '<tr><td colspan="3" class="empty-state">Owner permission is required to view the member directory.</td></tr>';
    return;
  }
  const payload = await api(`/v1/portal/organizations/${state.organization.id}/members`);
  $("#members-table").innerHTML = payload.data.map((member) => `<tr><td><strong>${escapeHtml(member.display_name)}</strong><br><small>${escapeHtml(member.email)}</small></td><td>${titleCase(member.role)}</td><td><span class="status-badge ${member.status === "active" ? "success" : "neutral"}">${titleCase(member.status)}</span></td></tr>`).join("") || '<tr><td colspan="3" class="empty-state">No members found.</td></tr>';
}

async function loadAudit() {
  if (state.organization.role !== "owner") return;
  const payload = await api(`/v1/portal/organizations/${state.organization.id}/audit-events?limit=100`);
  $("#audit-list").innerHTML = payload.data.length ? payload.data.map((event) => `<article class="audit-item"><time>${formatDate(event.created_at)}</time><div><strong>${escapeHtml(titleCase(event.action))}</strong><br><small>${escapeHtml(event.resource_type)}${event.resource_id ? ` · ${escapeHtml(event.resource_id)}` : ""}</small></div><small>${escapeHtml(titleCase(event.actor_type))}</small></article>`).join("") : '<p class="empty-state">No audit events yet.</p>';
}

function openDialog({ eyebrow, title, content }) {
  $("#dialog-eyebrow").textContent = eyebrow;
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = content;
  $("#form-dialog").showModal();
}

function bindEvents() {
  $$(".auth-tab").forEach((tab) => tab.addEventListener("click", () => showAuthPanel(tab.dataset.authTab)));
  $$('[data-return-login]').forEach((button) => button.addEventListener("click", () => showAuthPanel("login")));
  $("#show-reset").addEventListener("click", () => showAuthPanel("reset"));
  $$("[data-view]").forEach((button) => button.addEventListener("click", () => showPage(button.dataset.view)));
  $$('[data-go-view]').forEach((button) => button.addEventListener("click", () => showPage(button.dataset.goView)));
  $("#menu-button").addEventListener("click", () => $(".sidebar").classList.toggle("is-open"));

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const session = await api("/v1/portal/auth/login", { method: "POST", body: values });
      state.csrf = session.csrf_token; sessionStorage.setItem("llm_gateway_csrf", state.csrf);
      toast("Signed in successfully."); await loadAccount();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#register-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const result = await api("/v1/portal/auth/register", { method: "POST", body: values });
      if (result.verification_token) {
        $("#verify-form [name='token']").value = result.verification_token;
        showAuthPanel("verify");
      }
      toast("Organization created. Verify your email to continue.");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#verify-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const token = new FormData(event.currentTarget).get("token");
      await api("/v1/portal/auth/verify-email", { method: "POST", body: { token } });
      toast("Email verified. Sign in to continue."); showAuthPanel("login");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#reset-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const email = new FormData(event.currentTarget).get("email");
      const result = await api("/v1/portal/auth/password-reset/request", { method: "POST", body: { email } });
      toast(result.message);
      if (result.reset_token) openPasswordResetConfirm(result.reset_token);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#sign-out").addEventListener("click", async () => {
    try { await api("/v1/portal/auth/logout", { method: "POST" }); } catch (error) { toast(error.message, true); }
    state.csrf = ""; sessionStorage.removeItem("llm_gateway_csrf"); window.location.reload();
  });

  $("#organization-select").addEventListener("change", async (event) => {
    try {
      await api(`/v1/portal/auth/organizations/${event.target.value}/activate`, { method: "POST" });
      await loadAccount(); toast("Active organization changed.");
    } catch (error) { toast(error.message, true); renderOrganizationSwitcher(); }
  });

  $("#budget-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      values.limit_usd = Number(values.limit_usd); values.alert_threshold_percent = Number(values.alert_threshold_percent);
      await api(`/v1/portal/organizations/${state.organization.id}/budgets/${state.period}`, { method: "PUT", body: values });
      toast("Monthly budget updated."); await loadOverview();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#routing-preference-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const strategy = new FormData(event.currentTarget).get("strategy");
      await api(`/v1/portal/organizations/${state.organization.id}/routing/preference`, { method: "PUT", body: { strategy } });
      state.preference = strategy; renderPreference(strategy); toast("Routing preference saved.");
    } catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#new-key-button").addEventListener("click", openCreateKeyDialog);
  $("#invite-button").addEventListener("click", openInviteDialog);
  $("#keys-table").addEventListener("click", async (event) => {
    const button = event.target.closest(".revoke-key"); if (!button) return;
    if (!window.confirm("Revoke this API key? Applications using it will stop working.")) return;
    try { await api(`/v1/portal/organizations/${state.organization.id}/api-keys/${button.dataset.keyId}`, { method: "DELETE" }); toast("API key revoked."); await loadKeys(); }
    catch (error) { toast(error.message, true); }
  });
}

function openPasswordResetConfirm(token) {
  openDialog({ eyebrow: "PASSWORD RESET", title: "Choose a new password", content: `<form id="reset-confirm-form"><label>Reset token<input name="token" value="${escapeHtml(token)}" required></label><label>New password<input name="new_password" type="password" minlength="12" required></label><button class="button primary" type="submit">Change password</button></form>` });
  $("#reset-confirm-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try { const values = Object.fromEntries(new FormData(event.currentTarget)); await api("/v1/portal/auth/password-reset/confirm", { method: "POST", body: values }); $("#form-dialog").close(); toast("Password changed. Sign in with the new password."); showAuthPanel("login"); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(event.currentTarget, false); }
  });
}

function openCreateKeyDialog() {
  openDialog({ eyebrow: "NEW CREDENTIAL", title: "Create an API key", content: '<form id="create-key-form"><label>Key name<input name="name" maxlength="200" placeholder="Production app" required></label><p class="muted">The plaintext key will be shown once after creation.</p><button class="button primary" type="submit">Create key</button></form>' });
  $("#create-key-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const created = await api(`/v1/portal/organizations/${state.organization.id}/api-keys`, { method: "POST", body: values });
      $("#dialog-title").textContent = "Save this key now";
      $("#dialog-body").innerHTML = `<p class="muted">You will not be able to see it again.</p><div class="secret-box" id="new-key-secret">${escapeHtml(created.token)}</div><button id="copy-key" class="button primary" type="button">Copy key</button>`;
      $("#copy-key").addEventListener("click", async () => { await navigator.clipboard.writeText(created.token); toast("API key copied."); });
      await loadKeys();
    } catch (error) { toast(error.message, true); setBusy(event.currentTarget, false); }
  });
}

function openInviteDialog() {
  openDialog({ eyebrow: "TEAM INVITATION", title: "Invite a member", content: '<form id="invite-form"><label>Email<input name="email" type="email" required></label><label>Role<select name="role"><option value="developer">Developer</option><option value="billing_viewer">Billing viewer</option><option value="owner">Owner</option></select></label><button class="button primary" type="submit">Create invitation</button></form>' });
  $("#invite-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const invitation = await api(`/v1/portal/organizations/${state.organization.id}/invitations`, { method: "POST", body: values });
      $("#dialog-title").textContent = "Invitation created";
      $("#dialog-body").innerHTML = `<p class="muted">Development token for ${escapeHtml(invitation.email)}. A production email adapter will deliver this automatically.</p><div class="secret-box">${escapeHtml(invitation.invitation_token)}</div><button id="copy-invite" class="button primary" type="button">Copy invitation</button>`;
      $("#copy-invite").addEventListener("click", async () => { await navigator.clipboard.writeText(invitation.invitation_token); toast("Invitation copied."); });
    } catch (error) { toast(error.message, true); setBusy(event.currentTarget, false); }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadAccount().catch((error) => toast(error.message, true));
});
