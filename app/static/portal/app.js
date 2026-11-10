"use strict";

const state = {
  account: null,
  organization: null,
  csrf: sessionStorage.getItem("llm_gateway_csrf") || "",
  period: new Date().toISOString().slice(0, 7),
  usage: null,
  preference: "balanced",
  adminModels: [],
  adminEvaluationDatasets: [],
  adminEvaluationModels: [],
  adminRoutingPolicies: [],
  adminRoutingRollout: null,
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
  const canDevelop = ["owner", "developer"].includes(state.organization?.role);
  const isPlatformOperator = state.account?.platform_role === "platform_operator";
  $$(".owner-only:not([data-page])").forEach((element) => element.classList.toggle("hidden", !isOwner));
  $$(".developer-only:not([data-page])").forEach((element) => element.classList.toggle("hidden", !canDevelop));
  $$(".platform-only:not([data-page])").forEach((element) => element.classList.toggle("hidden", !isPlatformOperator));
  $('[data-view="audit"]').classList.toggle("hidden", !isOwner);
  if (!isOwner && $('[data-page="audit"]:not(.hidden)')) showPage("overview");
  if (!isOwner && $('[data-page="service-accounts"]:not(.hidden)')) showPage("overview");
  if (!canDevelop && $('[data-page="playground"]:not(.hidden)')) showPage("overview");
  if (!isPlatformOperator && $('[data-page="admin-models"]:not(.hidden)')) showPage("overview");
  if (!isPlatformOperator && $('[data-page="admin-evaluations"]:not(.hidden)')) showPage("overview");
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
  else if (usage.budget_alert_state === "threshold_reached") { badge.textContent = "Review spend"; badge.className = "status-badge warning"; }
  else { badge.textContent = "Within budget"; badge.className = "status-badge success"; }
  const alert = $("#budget-alert");
  if (["threshold_reached", "limit_reached"].includes(usage.budget_alert_state)) {
    alert.classList.remove("hidden", "danger");
    if (usage.budget_alert_state === "limit_reached") alert.classList.add("danger");
    $("#budget-alert-title").textContent = usage.budget_alert_state === "limit_reached" ? "Monthly budget reached" : "Budget alert threshold reached";
    $("#budget-alert-message").textContent = `${Number(usage.budget_utilization_percent).toFixed(1)}% of this month's ${formatMoney(usage.budget_usd)} allowance has been used. Your alert threshold is ${usage.budget_alert_threshold_percent}%.`;
  } else {
    alert.classList.add("hidden");
  }
  $("#usage-table").innerHTML = `<tr><td>${escapeHtml(usage.period)}</td><td>${formatNumber(usage.request_count)}</td><td>${formatNumber(usage.prompt_tokens)}</td><td>${formatNumber(usage.completion_tokens)}</td><td>${formatNumber(usage.total_tokens)}</td><td>${formatMoney(usage.cost_usd)}</td><td>${formatMoney(usage.remaining_budget_usd)}</td></tr>`;
  if (usage.budget_usd !== null) $("#budget-form [name='limit_usd']").value = usage.budget_usd;
  if (usage.budget_alert_threshold_percent !== null) $("#budget-form [name='alert_threshold_percent']").value = usage.budget_alert_threshold_percent;
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
  if (name === "playground") return;
  if (name === "keys") return loadKeys();
  if (name === "service-accounts") return loadServiceAccounts();
  if (name === "routing") return loadRouting();
  if (name === "admin-models") return loadAdminModels();
  if (name === "admin-evaluations") return loadAdminEvaluations();
  if (name === "team") return loadMembers();
  if (name === "audit") return loadAudit();
}

async function loadAdminEvaluations() {
  if (state.account?.platform_role !== "platform_operator") return;
  const [runs, scores, datasets, catalog, policies, rollout, summary] = await Promise.all([
    api("/v1/portal/admin/evaluations/runs?limit=100"),
    api("/v1/portal/admin/evaluations/scores"),
    api("/v1/portal/admin/evaluations/datasets"),
    api("/v1/portal/admin/models"),
    api("/v1/portal/admin/routing/policies"),
    api("/v1/portal/admin/routing/rollout"),
    api("/v1/portal/admin/routing/summary?window_hours=24"),
  ]);
  state.adminEvaluationDatasets = datasets.data;
  state.adminEvaluationModels = catalog.data.filter((model) => model.availability === "available");
  state.adminRoutingPolicies = policies.data;
  state.adminRoutingRollout = rollout;
  $("#evaluation-run-count").textContent = formatNumber(runs.data.length);
  $("#evaluation-completed-count").textContent = formatNumber(runs.data.filter((run) => run.status === "completed").length);
  $("#evaluation-failure-count").textContent = formatNumber(runs.data.reduce((total, run) => total + run.failed_case_count, 0));
  $("#score-version-count").textContent = formatNumber(scores.data.length);
  $("#evaluation-runs-table").innerHTML = runs.data.map((run) => {
    const statusClass = run.status === "completed" ? "success" : run.status === "failed" ? "danger" : run.status === "running" ? "warning" : "neutral";
    const actions = [
      run.status === "pending" ? `<button class="text-button execute-evaluation" data-run-id="${escapeHtml(run.id)}" data-run-label="${escapeHtml(`${run.provider} / ${run.model}`)}">Run</button>` : "",
      run.status === "completed" ? `<button class="text-button publish-evaluation-scores" data-run-id="${escapeHtml(run.id)}">Publish scores</button>` : "",
      run.failed_case_count > 0 ? `<button class="text-button review-evaluation-failures" data-run-id="${escapeHtml(run.id)}" data-run-label="${escapeHtml(`${run.provider} / ${run.model}`)}">Failures</button>` : "",
    ].filter(Boolean).join(" ");
    return `<tr><td><strong>${escapeHtml(run.dataset_name)}</strong><br><code>${escapeHtml(run.dataset_version)}</code></td><td>${escapeHtml(titleCase(run.provider))}<br><code>${escapeHtml(run.model)}</code></td><td><span class="status-badge ${statusClass}">${escapeHtml(titleCase(run.status))}</span></td><td>${formatNumber(run.case_count)}</td><td>${formatNumber(run.failed_case_count)}</td><td>${run.average_score === null ? "—" : `${Math.round(Number(run.average_score) * 100)} / 100`}</td><td>${formatMoney(run.total_cost_usd)}</td><td>${run.completed_at ? formatDate(run.completed_at) : "—"}</td><td>${actions}</td></tr>`;
  }).join("") || '<tr><td colspan="9" class="empty-state">No evaluation runs yet. Use the operator CLI to create and execute one.</td></tr>';
  $("#score-versions-table").innerHTML = scores.data.map((score) => {
    const statusClass = score.status === "active" ? "success" : score.status === "draft" ? "warning" : "neutral";
    const action = score.status === "draft" ? "activate" : score.status === "retired" ? "rollback" : "";
    const controls = `<button class="text-button compare-score-version" data-provider="${escapeHtml(score.provider)}" data-model="${escapeHtml(score.model)}" data-version="${escapeHtml(score.score_version)}">Compare</button>${action ? ` <button class="text-button change-score-activation" data-provider="${escapeHtml(score.provider)}" data-model="${escapeHtml(score.model)}" data-version="${escapeHtml(score.score_version)}" data-action="${action}">${escapeHtml(titleCase(action))}</button>` : ""}`;
    return `<tr><td><strong>${escapeHtml(titleCase(score.provider))}</strong><br><code>${escapeHtml(score.model)}</code></td><td><code>${escapeHtml(score.score_version)}</code></td><td><span class="status-badge ${statusClass}">${escapeHtml(titleCase(score.status))}</span></td><td>${Math.round(Number(score.weighted_quality_score) * 100)} / 100</td><td>${formatNumber(score.sample_count)}</td><td>${formatNumber(score.score_count)}</td><td>${score.task_categories.map((item) => escapeHtml(titleCase(item))).join(", ")}</td><td>${score.valid_until ? formatDate(score.valid_until) : "Not active"}</td><td>${controls}</td></tr>`;
  }).join("") || '<tr><td colspan="9" class="empty-state">No score versions have been generated.</td></tr>';
  const rolloutClass = rollout.mode === "live" ? "warning" : rollout.mode === "shadow" ? "success" : "neutral";
  $("#routing-rollout-mode").textContent = `${titleCase(rollout.mode)} routing`;
  $("#routing-rollout-source").textContent = `${titleCase(rollout.source)} · v${rollout.version ?? "—"}`;
  $("#routing-rollout-source").className = `status-badge ${rolloutClass}`;
  $("#routing-rollout-canary").textContent = `${formatNumber(rollout.canary_percent)}%`;
  $("#routing-rollout-allowlist").textContent = formatNumber(rollout.customer_allowlist.length);
  $("#routing-rollout-policy").textContent = rollout.required_policy_version || "Not pinned";
  $("#routing-rollout-score").textContent = rollout.required_score_version || "Not pinned";
  $("#routing-summary-requests").textContent = formatNumber(summary.request_count);
  const leadingRoute = summary.concentration[0];
  $("#routing-summary-concentration").textContent = leadingRoute ? `${leadingRoute.provider} / ${leadingRoute.model} · ${Math.round(leadingRoute.share * 100)}%` : "No routing data";
  $("#routing-summary-cost").textContent = formatMoney(summary.current_cost_usd);
  $("#routing-summary-drift").textContent = summary.cost_drift_percent === null ? "No prior-window comparison" : `${summary.cost_drift_percent >= 0 ? "+" : ""}${summary.cost_drift_percent.toFixed(1)}% vs prior 24h`;
  $("#routing-summary-evidence").textContent = summary.evidence_valid_until ? formatDate(summary.evidence_valid_until) : "Not active";
  $("#routing-summary-evidence-detail").textContent = summary.evidence_expired_count ? `${formatNumber(summary.evidence_expired_count)} expired score rows` : `${summary.active_policy_version || "No policy"} · ${summary.active_score_version || "No score set"}`;
  $("#routing-summary-alert").textContent = titleCase(summary.alert_state);
  $("#routing-summary-alert-detail").textContent = summary.alert_reasons.length ? summary.alert_reasons.map(titleCase).join(" · ") : "All routing safety checks pass";
  $("#routing-grafana-link").href = summary.grafana_url;
  $("#routing-concentration-list").innerHTML = summary.concentration.map((item) => `<div class="decision-item"><div><strong>${escapeHtml(titleCase(item.provider))} / ${escapeHtml(item.model)}</strong><p class="muted">${formatNumber(item.request_count)} requests</p></div><strong>${Math.round(item.share * 100)}%</strong></div>`).join("") || '<p class="empty-state">No routing decisions yet.</p>';
  $("#routing-classifier-list").innerHTML = summary.classifier_distribution.map((item) => `<div class="decision-item"><div><strong>${escapeHtml(titleCase(item.task_category))}</strong><p class="muted">${escapeHtml(titleCase(item.difficulty))} · ${formatNumber(item.request_count)} requests</p></div><strong>${Math.round(item.share * 100)}%</strong></div>`).join("") || '<p class="empty-state">No classified requests yet.</p>';
  $("#routing-policies-table").innerHTML = policies.data.map((policy) => {
    const statusClass = policy.status === "active" ? "success" : policy.status === "draft" ? "warning" : "neutral";
    const action = policy.status === "draft" ? "activate" : policy.status === "retired" ? "rollback" : "";
    const control = action ? `<button class="text-button change-routing-policy" data-version="${escapeHtml(policy.version)}" data-action="${action}">${escapeHtml(titleCase(action))}</button>` : "";
    return `<tr><td><code>${escapeHtml(policy.version)}</code></td><td><span class="status-badge ${statusClass}">${escapeHtml(titleCase(policy.status))}</span></td><td><code>${escapeHtml(policy.classifier_version)}</code></td><td><code>${escapeHtml(policy.score_version)}</code></td><td>${formatNumber(policy.minimum_sample_count)}</td><td>${escapeHtml(titleCase(policy.cold_start_mode))}</td><td>${policy.activated_at ? formatDate(policy.activated_at) : "—"}</td><td>${control}</td></tr>`;
  }).join("") || '<tr><td colspan="8" class="empty-state">No routing policies exist.</td></tr>';
}

function openRoutingRolloutDialog() {
  const rollout = state.adminRoutingRollout;
  if (!rollout) return;
  openDialog({ eyebrow: "CONTROLLED RELEASE", title: "Change routing rollout", content: `<form id="routing-rollout-form"><label>Mode<select name="mode"><option value="baseline">Baseline only</option><option value="shadow">Shadow observation</option><option value="live">Live canary</option></select></label><label>Live canary percentage<input name="canary_percent" type="number" min="0" max="100" value="${Number(rollout.canary_percent)}" required></label><label>Internal customer IDs<textarea name="customer_allowlist" rows="3" placeholder="One UUID per line">${escapeHtml(rollout.customer_allowlist.join("\n"))}</textarea></label><label>Policy version pin<input name="required_policy_version" maxlength="64" value="${escapeHtml(rollout.required_policy_version || "")}"></label><label>Score version pin<input name="required_score_version" maxlength="64" value="${escapeHtml(rollout.required_score_version || "")}"></label><label>Reason<textarea name="reason" rows="3" minlength="3" maxlength="500" required></textarea></label><label class="confirmation-check"><input name="confirmed" type="checkbox" required> I reviewed the evidence pins and rollout scope.</label><button class="button primary" type="submit">Apply rollout</button></form>` });
  const form = $("#routing-rollout-form");
  form.elements.mode.value = rollout.mode;
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const values = new FormData(form);
      const mode = values.get("mode");
      const baseline = mode === "baseline";
      const shadow = mode === "shadow";
      const allowlist = baseline || shadow ? [] : String(values.get("customer_allowlist") || "").split(/[\s,]+/).filter(Boolean);
      await api("/v1/portal/admin/routing/rollout", { method: "PUT", body: { mode, canary_percent: baseline || shadow ? 0 : Number(values.get("canary_percent")), customer_allowlist: allowlist, required_policy_version: baseline ? null : String(values.get("required_policy_version") || "").trim() || null, required_score_version: baseline ? null : String(values.get("required_score_version") || "").trim() || null, reason: values.get("reason"), confirmation: "CHANGE_ROUTING_ROLLOUT" } });
      $("#form-dialog").close(); toast("Routing rollout updated and audited."); await loadAdminEvaluations();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

function openEmergencyBaselineDialog() {
  openDialog({ eyebrow: "INCIDENT CONTROL", title: "Return all traffic to baseline", content: '<form id="emergency-baseline-form"><p>This immediately disables shadow and live intelligent routing, clears the canary and allowlist, and removes evidence pins.</p><label>Incident reason<textarea name="reason" rows="4" minlength="3" maxlength="500" required></textarea></label><label class="confirmation-check"><input name="confirmed" type="checkbox" required> I understand this changes routing for every customer.</label><button class="button danger" type="submit">Enable emergency baseline</button></form>' });
  const form = $("#emergency-baseline-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const reason = new FormData(form).get("reason");
      await api("/v1/portal/admin/routing/rollout/emergency-baseline", { method: "POST", body: { reason, confirmation: "EMERGENCY_BASELINE" } });
      $("#form-dialog").close(); toast("All traffic now uses baseline routing."); await loadAdminEvaluations();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

function openRoutingPolicyDialog(version, action) {
  openDialog({ eyebrow: "POLICY CHANGE", title: `${titleCase(action)} ${version}`, content: `<form id="routing-policy-form"><p>This changes the active rules used with pinned model evidence.</p><label>Reason<textarea name="reason" rows="4" minlength="3" maxlength="500" required></textarea></label><label class="confirmation-check"><input name="confirmed" type="checkbox" required> I reviewed the policy and understand the routing impact.</label><button class="button ${action === "rollback" ? "danger" : "primary"}" type="submit">${escapeHtml(titleCase(action))} policy</button></form>` });
  const form = $("#routing-policy-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const reason = new FormData(form).get("reason");
      await api("/v1/portal/admin/routing/policies/activation", { method: "POST", body: { version, action, reason, confirmation: "CHANGE_ROUTING_POLICY" } });
      $("#form-dialog").close(); toast(`Routing policy ${action === "rollback" ? "rolled back" : "activated"}.`); await loadAdminEvaluations();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

async function loadEvaluationFailures(runId, label) {
  const payload = await api(`/v1/portal/admin/evaluations/runs/${runId}/failures?limit=100`);
  $("#evaluation-failures-card").classList.remove("hidden");
  $("#evaluation-failures-title").textContent = `Failed cases · ${label}`;
  $("#evaluation-failures-table").innerHTML = payload.data.map((failure) => `<tr><td><code>${escapeHtml(failure.case_external_id)}</code></td><td>${escapeHtml(titleCase(failure.task_category))} · ${escapeHtml(titleCase(failure.difficulty))}</td><td>${escapeHtml(titleCase(failure.outcome))}</td><td>${failure.score === null ? "—" : `${Math.round(Number(failure.score) * 100)} / 100`}</td><td>${escapeHtml(failure.error_code || "—")}</td><td>${formatNumber(failure.prompt_tokens + failure.completion_tokens)}</td><td>${formatMoney(failure.cost_usd)}</td><td>${formatNumber(failure.latency_ms)} ms</td></tr>`).join("") || '<tr><td colspan="8" class="empty-state">No failed or zero-score cases were found.</td></tr>';
}

function openEvaluationDialog() {
  const datasets = state.adminEvaluationDatasets.map((dataset, index) => `<option value="${index}">${escapeHtml(dataset.name)} / ${escapeHtml(dataset.version)} · ${formatNumber(dataset.case_count)} cases</option>`).join("");
  const models = state.adminEvaluationModels.map((model, index) => `<option value="${index}">${escapeHtml(titleCase(model.provider))} / ${escapeHtml(model.model)}</option>`).join("");
  if (!datasets || !models) { toast("An active dataset and available model are required.", true); return; }
  openDialog({ eyebrow: "GUARDED EVALUATION", title: "Create an evaluation run", content: `<form id="evaluation-create-form"><label>Dataset<select name="dataset_index">${datasets}</select></label><label>Provider / model<select name="model_index">${models}</select></label><div id="evaluation-preflight" class="preview-card"><p class="muted">Preview the maximum token cost before creating the run. No provider call happens during preview.</p></div><button class="button primary" type="submit">Preview cost</button></form>` });
  const form = $("#evaluation-create-form");
  const clearPreview = () => { delete form.dataset.preflight; $("button[type='submit']", form).textContent = "Preview cost"; };
  $$('select', form).forEach((select) => select.addEventListener("change", clearPreview));
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const dataset = state.adminEvaluationDatasets[Number(form.elements.dataset_index.value)];
      const model = state.adminEvaluationModels[Number(form.elements.model_index.value)];
      const selection = { dataset_name: dataset.name, dataset_version: dataset.version, provider: model.provider, model: model.model };
      if (!form.dataset.preflight) {
        const preview = await api("/v1/portal/admin/evaluations/preflight", { method: "POST", body: selection });
        form.dataset.preflight = JSON.stringify(preview);
        $("#evaluation-preflight").innerHTML = `<p class="eyebrow">MAXIMUM ESTIMATE</p><strong>${formatMoney(preview.estimated_max_cost_usd)}</strong><p class="muted">${formatNumber(preview.case_count)} cases · up to ${formatNumber(preview.estimated_input_tokens + preview.maximum_output_tokens)} tokens${preview.paid_provider ? " · paid provider" : " · free provider"}</p><p class="muted">Creating this run does not call the provider. Execution remains a separate confirmation.</p>`;
        $("button[type='submit']", form).textContent = "Create pending run";
      } else {
        const preview = JSON.parse(form.dataset.preflight);
        await api("/v1/portal/admin/evaluations/runs", { method: "POST", body: { ...selection, approved_max_cost_usd: preview.estimated_max_cost_usd, confirmation: "CREATE_EVALUATION" } });
        $("#form-dialog").close(); toast("Pending evaluation created. Review it before running."); await loadAdminEvaluations();
      }
    } catch (error) { toast(error.message, true); clearPreview(); }
    finally { setBusy(form, false); }
  });
}

function openEvaluationExecutionDialog(runId, label) {
  openDialog({ eyebrow: "PAID ACTION", title: `Run ${label}`, content: `<form id="evaluation-execute-form"><p>This calls the configured provider for every unfinished case. The run will stop before starting if current prices exceed its approved ceiling.</p><label class="confirmation-check"><input name="confirmed" type="checkbox" required> I understand this may consume paid provider tokens.</label><button class="button danger" type="submit">Run evaluation</button></form>` });
  const form = $("#evaluation-execute-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const result = await api(`/v1/portal/admin/evaluations/runs/${runId}/execute`, { method: "POST", body: { confirmation: "RUN_EVALUATION" } });
      $("#form-dialog").close(); toast(`Evaluation completed: ${result.completed_cases} cases, ${formatMoney(result.total_cost_usd)}.`); await loadAdminEvaluations();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

function openScorePublishDialog(runId) {
  openDialog({ eyebrow: "VERSIONED EVIDENCE", title: "Publish draft scores", content: `<form id="score-publish-form"><label>Score version<input name="score_version" maxlength="64" placeholder="scores-2026-11" required></label><p class="muted preview-note">Only complete runs without failed cases can produce an immutable draft score version.</p><button class="button primary" type="submit">Publish draft</button></form>` });
  const form = $("#score-publish-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const score_version = String(new FormData(form).get("score_version") || "").trim();
      await api(`/v1/portal/admin/evaluations/runs/${runId}/scores`, { method: "POST", body: { score_version } });
      $("#form-dialog").close(); toast("Draft score version published."); await loadAdminEvaluations();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

async function openScoreComparisonDialog(provider, model, version) {
  const query = new URLSearchParams({ provider, model, score_version: version });
  const comparison = await api(`/v1/portal/admin/evaluations/scores/compare?${query}`);
  const rows = comparison.rows.map((row) => `<tr><td>${escapeHtml(titleCase(row.task_category))} · ${escapeHtml(titleCase(row.difficulty))}</td><td>${Math.round(Number(row.candidate_quality) * 100)} / 100</td><td>${row.active_quality === null ? "—" : `${Math.round(Number(row.active_quality) * 100)} / 100`}</td><td>${row.quality_delta === null ? "—" : `${Number(row.quality_delta) >= 0 ? "+" : ""}${Math.round(Number(row.quality_delta) * 100)} points`}</td><td>${formatNumber(row.candidate_samples)}</td></tr>`).join("");
  openDialog({ eyebrow: "EVIDENCE COMPARISON", title: `${provider} / ${model} · ${version}`, content: `<p class="muted">Active baseline: <code>${escapeHtml(comparison.active_version || "none")}</code></p><div class="table-wrap"><table><thead><tr><th>Task</th><th>Candidate</th><th>Active</th><th>Delta</th><th>Samples</th></tr></thead><tbody>${rows}</tbody></table></div>` });
}

function openScoreActivationDialog(provider, model, version, action) {
  openDialog({ eyebrow: "ROUTING EVIDENCE CHANGE", title: `${titleCase(action)} ${version}`, content: `<form id="score-activation-form"><p>This changes the quality evidence eligible for routing for <strong>${escapeHtml(provider)} / ${escapeHtml(model)}</strong>.</p><label>Reason<textarea name="reason" rows="4" minlength="3" maxlength="500" required placeholder="Record the evidence and operational reason."></textarea></label><label>Validity days<input name="valid_days" type="number" min="1" max="365" value="30" required></label><label class="confirmation-check"><input name="confirmed" type="checkbox" required> I reviewed the score comparison and understand the routing impact.</label><button class="button ${action === "rollback" ? "danger" : "primary"}" type="submit">${escapeHtml(titleCase(action))} score set</button></form>` });
  const form = $("#score-activation-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const values = new FormData(form);
      await api("/v1/portal/admin/evaluations/scores/activation", { method: "POST", body: { provider, model, score_version: version, action, reason: values.get("reason"), valid_days: Number(values.get("valid_days")), minimum_samples: 5, confirmation: "CHANGE_SCORE_SET" } });
      const verb = action === "rollback" ? "rolled back" : "activated";
      $("#form-dialog").close(); toast(`Score set ${verb}.`); await loadAdminEvaluations();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

async function loadAdminModels() {
  if (state.account?.platform_role !== "platform_operator") return;
  const [catalog, proposals] = await Promise.all([
    api("/v1/portal/admin/models"),
    api("/v1/portal/admin/pricing-proposals?limit=100"),
  ]);
  state.adminModels = catalog.data;
  $("#model-catalog-table").innerHTML = catalog.data.map((model) => {
    const available = model.availability === "available";
    const context = model.context_window_tokens === null
      ? '<span class="muted">Not declared</span>'
      : `${formatNumber(model.context_window_tokens)} tokens`;
    const capabilities = model.capabilities
      .map((capability) => `<span class="catalog-chip">${escapeHtml(titleCase(capability))}</span>`)
      .join("");
    const routing = model.auto_selectable
      ? `Automatic · up to ${escapeHtml(titleCase(model.max_complexity))}`
      : "Explicit only";
    return `<tr><td><strong>${escapeHtml(titleCase(model.provider))}</strong><br><code>${escapeHtml(model.model)}</code></td><td><span class="status-badge ${available ? "success" : "neutral"}">${escapeHtml(titleCase(model.availability))}</span></td><td>${formatMoney(model.input_cost_per_million_usd)}</td><td>${formatMoney(model.output_cost_per_million_usd)}</td><td>${context}</td><td><div class="catalog-chips">${capabilities}</div></td><td>${formatNumber(model.estimated_latency_ms)} ms</td><td>${Math.round(Number(model.quality_score) * 100)} / 100</td><td>${routing}</td></tr>`;
  }).join("") || '<tr><td colspan="9" class="empty-state">No models are configured.</td></tr>';
  $("#pricing-proposals-table").innerHTML = proposals.data.map((proposal) => {
    const statusClass = proposal.status === "approved" ? "success" : proposal.status === "rejected" ? "danger" : "warning";
    const canReview = proposal.status === "pending" && proposal.proposed_by_user_id !== state.account.user_id;
    const actions = canReview
      ? `<button class="text-button review-pricing" data-proposal-id="${escapeHtml(proposal.id)}" data-decision="approved">Approve</button> <button class="text-button review-pricing" data-proposal-id="${escapeHtml(proposal.id)}" data-decision="rejected">Reject</button>`
      : proposal.status === "pending" ? '<span class="muted">Independent reviewer required</span>' : "";
    return `<tr><td><strong>${escapeHtml(titleCase(proposal.provider))}</strong><br><code>${escapeHtml(proposal.model)}</code></td><td>${formatMoney(proposal.input_cost_per_million_usd)}</td><td>${formatMoney(proposal.output_cost_per_million_usd)}</td><td><a href="${escapeHtml(proposal.source_url)}" target="_blank" rel="noopener noreferrer">Evidence ↗</a></td><td><span class="status-badge ${statusClass}">${escapeHtml(titleCase(proposal.status))}</span></td><td>${formatDate(proposal.created_at)}</td><td>${actions}</td></tr>`;
  }).join("") || '<tr><td colspan="7" class="empty-state">No pricing changes have been proposed.</td></tr>';
}

function openPricingProposalDialog() {
  const options = state.adminModels
    .filter((model) => ["openai", "anthropic", "local"].includes(model.provider))
    .map((model) => `<option value="${escapeHtml(model.provider)}" data-model="${escapeHtml(model.model)}" data-input="${model.input_cost_per_million_usd}" data-output="${model.output_cost_per_million_usd}">${escapeHtml(titleCase(model.provider))} / ${escapeHtml(model.model)}</option>`)
    .join("");
  openDialog({ eyebrow: "PRICING CONTROL", title: "Propose a price update", content: `<form id="pricing-proposal-form"><label>Provider / model<select name="provider">${options}</select></label><label>Model<input name="model" maxlength="128" required></label><div class="form-grid two"><label>Input USD per 1M tokens<input name="input_cost_per_million_usd" type="number" min="0" step="0.000001" required></label><label>Output USD per 1M tokens<input name="output_cost_per_million_usd" type="number" min="0" step="0.000001" required></label></div><label>Official pricing source URL<input name="source_url" type="url" placeholder="https://provider.example/pricing" required></label><label>Reason for change<textarea name="rationale" rows="3" minlength="10" maxlength="1000" required></textarea></label><p class="muted preview-note">A different platform operator must review this proposal. Approval does not silently change live routing.</p><button class="button primary" type="submit">Submit proposal</button></form>` });
  const form = $("#pricing-proposal-form");
  const provider = form.elements.provider;
  const syncModel = () => {
    const selected = provider.selectedOptions[0];
    form.elements.model.value = selected?.dataset.model || "";
    form.elements.input_cost_per_million_usd.value = selected?.dataset.input || "";
    form.elements.output_cost_per_million_usd.value = selected?.dataset.output || "";
  };
  provider.addEventListener("change", syncModel); syncModel();
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const values = Object.fromEntries(new FormData(form));
      values.input_cost_per_million_usd = Number(values.input_cost_per_million_usd);
      values.output_cost_per_million_usd = Number(values.output_cost_per_million_usd);
      await api("/v1/portal/admin/pricing-proposals", { method: "POST", body: values });
      $("#form-dialog").close(); toast("Pricing proposal submitted for independent review.");
      await loadAdminModels();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

function openPricingReviewDialog(proposalId, decision) {
  openDialog({ eyebrow: "INDEPENDENT REVIEW", title: `${titleCase(decision)} price proposal`, content: `<form id="pricing-review-form"><label>Review note<textarea name="review_note" rows="4" minlength="3" maxlength="1000" required placeholder="Record the evidence you checked and why."></textarea></label><p class="muted preview-note">This decision is written to the immutable platform audit trail.</p><button class="button ${decision === "rejected" ? "danger" : "primary"}" type="submit">${escapeHtml(titleCase(decision))}</button></form>` });
  const form = $("#pricing-review-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(form, true);
    try {
      const review_note = new FormData(form).get("review_note");
      await api(`/v1/portal/admin/pricing-proposals/${proposalId}/review`, { method: "POST", body: { decision, review_note } });
      $("#form-dialog").close(); toast(`Pricing proposal ${decision}.`); await loadAdminModels();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(form, false); }
  });
}

async function loadKeys() {
  const payload = await api(`/v1/portal/organizations/${state.organization.id}/api-keys`);
  $("#keys-table").innerHTML = payload.data.length ? payload.data.map((key) => `<tr><td><strong>${escapeHtml(key.name)}</strong></td><td><code>${escapeHtml(key.key_prefix)}…</code></td><td>${formatDate(key.created_at)}</td><td><span class="status-badge ${key.revoked_at ? "neutral" : "success"}">${key.revoked_at ? "Revoked" : "Active"}</span></td><td>${key.revoked_at ? "" : `<button class="text-button rotate-key" data-key-id="${escapeHtml(key.id)}">Rotate</button> <button class="text-button revoke-key" data-key-id="${escapeHtml(key.id)}">Revoke</button>`}</td></tr>`).join("") : '<tr><td colspan="5" class="empty-state">No API keys yet.</td></tr>';
}

async function loadServiceAccounts() {
  if (state.organization.role !== "owner") return;
  const payload = await api(`/v1/portal/organizations/${state.organization.id}/service-accounts`);
  $("#service-accounts-table").innerHTML = payload.data.length ? payload.data.map((account) => `<tr><td><strong>${escapeHtml(account.name)}</strong></td><td>${titleCase(account.role)}</td><td>${account.key_prefix ? `<code>${escapeHtml(account.key_prefix)}…</code>` : "—"}</td><td>${formatDate(account.created_at)}</td><td><span class="status-badge ${account.status === "active" ? "success" : "neutral"}">${titleCase(account.status)}</span></td><td>${account.status === "active" ? `<button class="text-button revoke-service-account" data-service-account-id="${escapeHtml(account.id)}">Revoke</button>` : ""}</td></tr>`).join("") : '<tr><td colspan="6" class="empty-state">No service accounts yet.</td></tr>';
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

function playgroundPayload() {
  const form = $("#playground-form");
  const values = new FormData(form);
  const systemPrompt = String(values.get("system_prompt") || "").trim();
  const userPrompt = String(values.get("prompt") || "").trim();
  if (!userPrompt) throw new Error("Enter a user prompt first.");
  const messages = [];
  if (systemPrompt) messages.push({ role: "system", content: systemPrompt });
  messages.push({ role: "user", content: userPrompt });
  const allowedProviders = $$("input[name='provider']:checked", form).map((input) => input.value);
  const maxCost = String(values.get("max_cost_usd") || "").trim();
  const routing = {
    strategy: values.get("strategy"),
    disable_cache: values.get("disable_cache") === "on",
  };
  if (allowedProviders.length) routing.allowed_providers = allowedProviders;
  if (maxCost) routing.max_cost_usd = Number(maxCost);
  return {
    model: String(values.get("model") || "auto").trim() || "auto",
    messages,
    temperature: Number(values.get("temperature")),
    max_tokens: Number(values.get("max_tokens")),
    routing,
  };
}

function renderPlaygroundPreview(preview) {
  $("#playground-preview").classList.remove("hidden");
  $("#preview-model").textContent = `${preview.selected_provider} / ${preview.selected_model}`;
  $("#preview-cost").textContent = `≤ ${formatMoney(preview.estimated_cost_usd)}`;
  $("#preview-task").textContent = `${titleCase(preview.task_category)} · ${titleCase(preview.difficulty)}`;
  $("#preview-policy").textContent = `${preview.policy_version} · ${preview.score_version}`;
  $("#preview-candidates").textContent = `${formatNumber(preview.eligible_candidate_count)} eligible model${preview.eligible_candidate_count === 1 ? "" : "s"}`;
  $("#preview-reasons").innerHTML = preview.reason_codes.length
    ? preview.reason_codes.map((reason) => `<li>${escapeHtml(titleCase(reason))}</li>`).join("")
    : `<li>${escapeHtml(titleCase(preview.routing_strategy))} strategy</li>`;
}

function renderPlaygroundResponse(result, elapsedMs) {
  const gateway = result.gateway;
  const usage = result.usage;
  const statusBadge = $("#playground-status");
  statusBadge.textContent = gateway.cached ? "Cached" : "Completed";
  statusBadge.className = `status-badge ${gateway.cached ? "neutral" : "success"}`;
  $("#playground-metrics").classList.remove("hidden");
  $("#response-model").textContent = `${gateway.provider} / ${result.model}`;
  $("#response-cost").textContent = formatMoney(gateway.cost_usd);
  $("#response-tokens").textContent = formatNumber(usage.total_tokens);
  $("#response-latency").textContent = `${Math.round(elapsedMs)} ms`;
  const output = $("#playground-output");
  output.classList.remove("empty-state");
  output.textContent = result.choices[0]?.message?.content || "The provider returned no text.";
  $("#playground-routing").classList.remove("hidden");
  $("#response-route-summary").textContent = `${titleCase(gateway.task_category)} · ${titleCase(gateway.task_complexity)} · ${Math.round(gateway.classification_confidence * 100)}% confidence · ${gateway.routing_policy_version} · ${gateway.score_version}`;
  $("#response-reasons").innerHTML = gateway.routing_reasons.length
    ? gateway.routing_reasons.map((reason) => `<li>${escapeHtml(titleCase(reason))}</li>`).join("")
    : `<li>${escapeHtml(titleCase(gateway.routing_strategy))} strategy</li>`;
  $("#response-attempts").innerHTML = gateway.attempts.length
    ? gateway.attempts.map((attempt) => `<li>${escapeHtml(attempt.provider)} / ${escapeHtml(attempt.model)} — ${escapeHtml(titleCase(attempt.outcome))}</li>`).join("")
    : "<li>No provider call — response served from cache.</li>";
}

function bindEvents() {
  $$(".auth-tab").forEach((tab) => tab.addEventListener("click", () => showAuthPanel(tab.dataset.authTab)));
  $$('[data-return-login]').forEach((button) => button.addEventListener("click", () => showAuthPanel("login")));
  $("#show-reset").addEventListener("click", () => showAuthPanel("reset"));
  $("#show-resend").addEventListener("click", () => showAuthPanel("resend"));
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
        toast("Organization created. Verify your email to continue.");
      } else {
        $("#login-form [name='email']").value = values.email;
        showAuthPanel("login");
        toast("Organization created. Check your email for the verification link.");
      }
    } catch (error) {
      toast(error.message, true);
      if (error.code === "email_delivery_failed") {
        const email = new FormData(event.currentTarget).get("email");
        $("#resend-verification-form [name='email']").value = email;
        showAuthPanel("resend");
      }
    }
    finally { setBusy(event.currentTarget, false); }
  });

  $("#invitation-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const session = await api("/v1/portal/invitations/accept-new", { method: "POST", body: values });
      state.csrf = session.csrf_token; sessionStorage.setItem("llm_gateway_csrf", state.csrf);
      toast(`Welcome to ${session.active_organization.name}.`); await loadAccount();
    } catch (error) {
      toast(error.message, true);
      if (error.code === "account_exists") showAuthPanel("login");
    } finally { setBusy(event.currentTarget, false); }
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

  $("#resend-verification-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const email = new FormData(event.currentTarget).get("email");
      const result = await api("/v1/portal/auth/verify-email/resend", { method: "POST", body: { email } });
      if (result.verification_token) {
        $("#verify-form [name='token']").value = result.verification_token;
        showAuthPanel("verify");
      } else {
        showAuthPanel("login");
      }
      toast(result.message);
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

  $("#preview-request").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const previousLabel = button.textContent;
    button.disabled = true; button.textContent = "Planning…";
    try {
      const preview = await api(`/v1/portal/organizations/${state.organization.id}/playground/preview`, { method: "POST", body: playgroundPayload() });
      renderPlaygroundPreview(preview); toast("Route preview updated.");
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = previousLabel; }
  });

  $("#playground-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    const statusBadge = $("#playground-status");
    statusBadge.textContent = "Running…"; statusBadge.className = "status-badge warning";
    const started = performance.now();
    try {
      const result = await api(`/v1/portal/organizations/${state.organization.id}/playground/completions`, { method: "POST", body: playgroundPayload() });
      renderPlaygroundResponse(result, performance.now() - started);
      loadOverview().catch((error) => toast(error.message, true));
    } catch (error) {
      statusBadge.textContent = "Failed"; statusBadge.className = "status-badge danger";
      toast(error.message, true);
    } finally { setBusy(event.currentTarget, false); }
  });

  $("#new-key-button").addEventListener("click", openCreateKeyDialog);
  $("#new-service-account-button").addEventListener("click", openCreateServiceAccountDialog);
  $("#new-pricing-proposal-button").addEventListener("click", openPricingProposalDialog);
  $("#new-evaluation-button").addEventListener("click", openEvaluationDialog);
  $("#edit-routing-rollout").addEventListener("click", openRoutingRolloutDialog);
  $("#emergency-routing-baseline").addEventListener("click", openEmergencyBaselineDialog);
  $("#invite-button").addEventListener("click", openInviteDialog);
  $("#pricing-proposals-table").addEventListener("click", (event) => {
    const button = event.target.closest(".review-pricing");
    if (button) openPricingReviewDialog(button.dataset.proposalId, button.dataset.decision);
  });
  $("#evaluation-runs-table").addEventListener("click", (event) => {
    const execute = event.target.closest(".execute-evaluation");
    if (execute) { openEvaluationExecutionDialog(execute.dataset.runId, execute.dataset.runLabel); return; }
    const publish = event.target.closest(".publish-evaluation-scores");
    if (publish) { openScorePublishDialog(publish.dataset.runId); return; }
    const failures = event.target.closest(".review-evaluation-failures");
    if (failures) loadEvaluationFailures(failures.dataset.runId, failures.dataset.runLabel).catch((error) => toast(error.message, true));
  });
  $("#score-versions-table").addEventListener("click", (event) => {
    const compare = event.target.closest(".compare-score-version");
    if (compare) { openScoreComparisonDialog(compare.dataset.provider, compare.dataset.model, compare.dataset.version).catch((error) => toast(error.message, true)); return; }
    const activation = event.target.closest(".change-score-activation");
    if (activation) openScoreActivationDialog(activation.dataset.provider, activation.dataset.model, activation.dataset.version, activation.dataset.action);
  });
  $("#routing-policies-table").addEventListener("click", (event) => {
    const button = event.target.closest(".change-routing-policy");
    if (button) openRoutingPolicyDialog(button.dataset.version, button.dataset.action);
  });
  $("#keys-table").addEventListener("click", async (event) => {
    const rotateButton = event.target.closest(".rotate-key");
    if (rotateButton) {
      if (!window.confirm("Rotate this API key? The current key will stop working immediately.")) return;
      try {
        const rotated = await api(`/v1/portal/organizations/${state.organization.id}/api-keys/${rotateButton.dataset.keyId}/rotate`, { method: "POST" });
        showOneTimeSecret("Save the replacement key", rotated.token, "The previous key is revoked. Update the application before closing this dialog.");
        await loadKeys();
      } catch (error) { toast(error.message, true); }
      return;
    }
    const button = event.target.closest(".revoke-key"); if (!button) return;
    if (!window.confirm("Revoke this API key? Applications using it will stop working.")) return;
    try { await api(`/v1/portal/organizations/${state.organization.id}/api-keys/${button.dataset.keyId}`, { method: "DELETE" }); toast("API key revoked."); await loadKeys(); }
    catch (error) { toast(error.message, true); }
  });
  $("#service-accounts-table").addEventListener("click", async (event) => {
    const button = event.target.closest(".revoke-service-account"); if (!button) return;
    if (!window.confirm("Revoke this service account and its credential?")) return;
    try {
      await api(`/v1/portal/organizations/${state.organization.id}/service-accounts/${button.dataset.serviceAccountId}`, { method: "DELETE" });
      toast("Service account revoked."); await loadServiceAccounts();
    } catch (error) { toast(error.message, true); }
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
      showOneTimeSecret("Save this key now", created.token, "You will not be able to see it again.");
      await loadKeys();
    } catch (error) { toast(error.message, true); setBusy(event.currentTarget, false); }
  });
}

function showOneTimeSecret(title, token, message) {
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = `<p class="muted">${escapeHtml(message)}</p><div class="secret-box">${escapeHtml(token)}</div><button id="copy-secret" class="button primary" type="button">Copy credential</button>`;
  $("#copy-secret").addEventListener("click", async () => { await navigator.clipboard.writeText(token); toast("Credential copied."); });
  if (!$("#form-dialog").open) $("#form-dialog").showModal();
}

function openCreateServiceAccountDialog() {
  openDialog({ eyebrow: "WORKLOAD IDENTITY", title: "Create a service account", content: '<form id="create-service-account-form"><label>Name<input name="name" maxlength="200" placeholder="Production worker" required></label><p class="muted">A developer credential will be shown once. It can call the gateway but cannot sign in to the portal.</p><button class="button primary" type="submit">Create service account</button></form>' });
  $("#create-service-account-form").addEventListener("submit", async (event) => {
    event.preventDefault(); setBusy(event.currentTarget, true);
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const created = await api(`/v1/portal/organizations/${state.organization.id}/service-accounts`, { method: "POST", body: values });
      showOneTimeSecret("Save the service credential", created.token, "This credential is shown once. Store it in your application's secret manager.");
      await loadServiceAccounts();
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
      if (!invitation.invitation_token) {
        $("#form-dialog").close();
        toast(`Invitation emailed to ${invitation.email}.`);
        return;
      }
      const invitationLink = `${window.location.origin}/portal/#invitation=${encodeURIComponent(invitation.invitation_token)}`;
      $("#dialog-title").textContent = "Invitation created";
      $("#dialog-body").innerHTML = `<p class="muted">Development link for ${escapeHtml(invitation.email)}. A production email adapter will deliver this automatically.</p><div class="secret-box">${escapeHtml(invitationLink)}</div><button id="copy-invite" class="button primary" type="button">Copy invitation link</button>`;
      $("#copy-invite").addEventListener("click", async () => { await navigator.clipboard.writeText(invitationLink); toast("Invitation link copied."); });
    } catch (error) { toast(error.message, true); setBusy(event.currentTarget, false); }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  const action = new URLSearchParams(window.location.hash.slice(1));
  const invitationToken = action.get("invitation");
  const verificationToken = action.get("verify");
  const resetToken = action.get("reset");
  if (invitationToken || verificationToken || resetToken) {
    window.history.replaceState({}, "", `${window.location.pathname}${window.location.search}`);
  }
  if (invitationToken) {
    $("#invitation-form [name='token']").value = invitationToken;
    showAuthPanel("invitation");
    return;
  }
  if (verificationToken) {
    $("#verify-form [name='token']").value = verificationToken;
    showAuthPanel("verify");
    return;
  }
  if (resetToken) {
    showAuthPanel("login");
    openPasswordResetConfirm(resetToken);
    return;
  }
  loadAccount().catch((error) => toast(error.message, true));
});
