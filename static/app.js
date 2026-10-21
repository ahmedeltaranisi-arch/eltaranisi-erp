/* ============================================================
   Eltaranisi ERP — front-end core (state, API, shell, router, UI kit)
   ============================================================ */
const APP = {
  token: localStorage.getItem("erp_token") || null,
  user: null,
  meta: null,
  lang: localStorage.getItem("erp_lang") || "ar",
  theme: localStorage.getItem("erp_theme") || "light",
  route: null,
  cache: {},
};
window.APP = APP;
window.PAGES = window.PAGES || {};

/* ------------------------------ helpers ------------------------------ */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const ESC = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtMoney(cents, opts = {}) {
  const v = (Number(cents) || 0) / 100;
  const s = v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (opts.plain) return s;
  return `<span class="money"><span class="num">${s}</span><span class="cur">${APP.lang === "ar" ? "ج.م" : "EGP"}</span></span>`;
}
function moneyText(cents) {
  // chart labels / donut totals: values are stored in cents, shown in currency units
  const v = (Number(cents) || 0) / 100;
  const opts = Math.abs(v) >= 100000
    ? { maximumFractionDigits: 0 }
    : { minimumFractionDigits: 2, maximumFractionDigits: 2 };
  return v.toLocaleString("en-US", opts);
}
function fmtNum(n, digits = 0) {
  return (Number(n) || 0).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
function fmtDate(d) {
  if (!d) return "—";
  return String(d).slice(0, 10);
}
function pct(part, whole) {
  if (!whole) return "0%";
  return ((part / whole) * 100).toFixed(1) + "%";
}
function statusBadge(status) {
  const map = {
    draft: "gray", review: "amber", approved: "blue", posted: "green", reversed: "red", cancelled: "red",
    paid: "green", partially_paid: "amber", open: "green", closed: "gray", active: "green", inactive: "gray",
  };
  return `<span class="badge ${map[status] || "gray"}">${ESC(t("status." + status, status))}</span>`;
}
function can(perm) {
  if (!APP.user) return false;
  const p = APP.user.permissions || [];
  if (p.includes("*") || p.includes(perm)) return true;
  return p.includes(perm.split(".")[0] + ".*");
}
function initials(name) {
  return String(name || "?").trim().split(/\s+/).slice(0, 2).map(w => w[0]).join("");
}

/* ------------------------------ API ------------------------------ */
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (APP.token) headers["Authorization"] = "Bearer " + APP.token;
  const loading = $("#loading-bar");
  loading.classList.remove("hidden");
  try {
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    const isJson = (res.headers.get("content-type") || "").includes("json");
    const data = isJson ? await res.json() : await res.text();
    if (!res.ok) {
      if (res.status === 401) { logoutLocal(); throw new Error(t("error.unauthenticated")); }
      const msg = (data && data.error) || `HTTP ${res.status}`;
      const err = new Error(msg);
      err.details = data && data.details;
      err.status = res.status;
      throw err;
    }
    return data;
  } finally {
    loading.classList.add("hidden");
  }
}

/* ------------------------------ toast / modal ------------------------------ */
function toast(msg, kind = "ok") {
  const root = $("#toast-root");
  const div = document.createElement("div");
  div.className = "toast " + kind;
  div.innerHTML = `<span>${kind === "err" ? "⚠️" : "✅"}</span><div>${ESC(msg)}</div>`;
  root.appendChild(div);
  setTimeout(() => { div.style.opacity = "0"; div.style.transform = "translateY(8px)"; }, 3600);
  setTimeout(() => div.remove(), 4100);
}

function openModal({ title, body, footer = "", wide = false, narrow = false, onMount, onClose }) {
  const root = $("#modal-root");
  root.classList.remove("hidden");
  root.innerHTML = `
    <div class="modal ${wide ? "wide" : ""} ${narrow ? "narrow" : ""}">
      <div class="modal-head">
        <h3>${ESC(title)}</h3><div class="spacer"></div>
        <button class="icon-btn" data-close>✕</button>
      </div>
      <div class="modal-body">${body}</div>
      ${footer ? `<div class="modal-foot">${footer}</div>` : ""}
    </div>`;
  const close = () => { root.classList.add("hidden"); root.innerHTML = ""; if (onClose) onClose(); };
  root.querySelectorAll("[data-close]").forEach(b => b.onclick = close);
  root.onclick = (e) => { if (e.target === root) close(); };
  if (onMount) onMount(root, close);
  return { root, close };
}

function confirmDialog(message, onConfirm, { danger = false, title = "تأكيد" } = {}) {
  openModal({
    title, narrow: true,
    body: `<p style="margin:0;font-size:14px">${ESC(message)}</p>`,
    footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
             <button class="btn ${danger ? "btn-danger" : "btn-primary"}" id="cf-ok">${ESC(t("common.approve"))}</button>`,
    onMount: (root, close) => {
      $("#cf-ok", root).onclick = async () => { close(); await onConfirm(); };
    }
  });
}

/**
 * Generic form modal.
 * fields: [{ name, label, type, options, value, required, hint, step, min, max, rows }]
 */
function openForm({ title, fields, submitLabel, onSubmit, wide, extraHtml = "" }) {
  const body = `<form id="gen-form" autocomplete="off">
      <div class="grid cols-2" style="gap:0 16px">
      ${fields.map(f => fieldHtml(f)).join("")}
      </div>
      ${extraHtml}
      <div class="form-error hidden" id="gen-error"></div>
    </form>`;
  openModal({
    title, body, wide,
    footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
             <button class="btn btn-primary" id="gen-submit">${ESC(submitLabel || t("common.save"))}</button>`,
    onMount: (root, close) => {
      const form = $("#gen-form", root);
      const submit = async () => {
        const data = {};
        for (const f of fields) {
          const el = form.querySelector(`[name="${f.name}"]`);
          if (!el) continue;
          let v = f.type === "checkbox" ? el.checked : el.value;
          if (f.required && (v === "" || v === null || v === undefined)) {
            showErr(root, `${f.label}: ${t("common.required")}`);
            el.focus();
            return;
          }
          data[f.name] = v;
        }
        try {
          $("#gen-submit", root).disabled = true;
          await onSubmit(data, root);
          close();
        } catch (e) {
          showErr(root, e.message);
        } finally {
          const b = $("#gen-submit", root);
          if (b) b.disabled = false;
        }
      };
      $("#gen-submit", root).onclick = submit;
      form.onsubmit = (e) => { e.preventDefault(); submit(); };
      form.querySelectorAll("input,select,textarea").forEach(el => {
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && el.tagName !== "TEXTAREA") { e.preventDefault(); submit(); }
        });
      });
    }
  });
}
function showErr(root, msg) {
  const box = $("#gen-error", root) || $("#login-error");
  if (!box) return;
  box.textContent = msg;
  box.classList.remove("hidden");
}
function fieldHtml(f) {
  const val = f.value ?? "";
  const common = `name="${f.name}" id="fld-${f.name}" ${f.required ? "required" : ""}`;
  let input;
  if (f.type === "select") {
    input = `<select ${common}>${(f.options || []).map(o =>
      `<option value="${ESC(o.value)}" ${String(o.value) === String(val) ? "selected" : ""}>${ESC(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "textarea") {
    input = `<textarea ${common} rows="${f.rows || 3}">${ESC(val)}</textarea>`;
  } else if (f.type === "checkbox") {
    input = `<label class="checkbox"><input type="checkbox" ${common} ${val ? "checked" : ""}/> ${ESC(f.label)}</label>`;
    return `<div class="field" style="grid-column:span 2">${input}${f.hint ? `<div class="field-hint">${ESC(f.hint)}</div>` : ""}</div>`;
  } else {
    input = `<input ${common} type="${f.type || "text"}" value="${ESC(val)}" ${f.step ? `step="${f.step}"` : ""} ${f.min !== undefined ? `min="${f.min}"` : ""} ${f.placeholder ? `placeholder="${ESC(f.placeholder)}"` : ""}/>`;
  }
  return `<div class="field" style="${f.span === 2 ? "grid-column:span 2" : ""}">
      <span>${ESC(f.label)}${f.required ? " *" : ""}</span>${input}
      ${f.hint ? `<div class="field-hint">${ESC(f.hint)}</div>` : ""}</div>`;
}

/* ------------------------------ tables ------------------------------ */
/**
 * cols: [{ key, label, cls, render(row), sortable }]
 * opts: { footer, onRowClick, empty }
 */
function renderTable(cols, rows, opts = {}) {
  if (!rows || !rows.length) {
    return `<div class="empty"><span class="big">◍</span>${ESC(opts.empty || t("common.empty"))}</div>`;
  }
  const head = cols.map(c => `<th class="${c.cls || ""}">${ESC(c.label)}</th>`).join("");
  const body = rows.map((r, i) => {
    const tds = cols.map(c => `<td class="${c.cls || ""}" data-label="${ESC(c.label || "")}">${c.render ? c.render(r, i) : ESC(r[c.key] ?? "—")}</td>`).join("");
    return `<tr class="${opts.onRowClick ? "row-click" : ""}" data-idx="${i}">${tds}</tr>`;
  }).join("");
  const foot = opts.footer ? `<tfoot>${opts.footer}</tfoot>` : "";
  const html = `<div class="table-wrap"><table class="data ${opts.compact ? "compact" : ""}">
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody>${foot}</table></div>`;
  return html;
}
function bindTable(root, opts = {}) {
  if (!opts.onRowClick) return;
  $$("tbody tr", root).forEach(tr => {
    tr.onclick = (e) => {
      if (e.target.closest("button,a,input,select")) return;
      opts.onRowClick(Number(tr.dataset.idx));
    };
  });
}
function card(title, bodyHtml, actionsHtml = "", extra = "") {
  return `<div class="card ${extra}">
      ${title ? `<div class="card-head"><h3>${ESC(title)}</h3><div class="spacer"></div>${actionsHtml}</div>` : ""}
      <div class="card-body ${bodyHtml.startsWith("<div class=\"table-wrap\"") ? "tight" : ""}">${bodyHtml}</div>
    </div>`;
}
function kpi({ label, value, sub, icon, tone = "", raw = false }) {
  return `<div class="card kpi ${tone}">
      <div class="kpi-ico">${icon}</div>
      <div>
        <div class="kpi-label">${ESC(label)}</div>
        <div class="kpi-value">${raw ? value : fmtMoney(value)}</div>
        ${sub ? `<div class="kpi-sub">${sub}</div>` : ""}
      </div>
    </div>`;
}

/* ------------------------------ file exports ------------------------------ */
async function apiBlob(path) {
  const res = await fetch(path, { headers: APP.token ? { Authorization: "Bearer " + APP.token } : {} });
  if (!res.ok) {
    let msg = "HTTP " + res.status;
    try { const j = await res.json(); msg = j.error || msg; } catch (e) { /* binary error */ }
    throw new Error(msg);
  }
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename="([^"]+)"/.exec(cd);
  return { blob: await res.blob(), filename: m ? m[1] : null };
}
async function downloadApi(path, fallbackName = "export") {
  try {
    toast(APP.lang === "ar" ? "جارٍ تجهيز الملف…" : "Preparing the file…", "ok", 1400);
    const { blob, filename } = await apiBlob(path);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || fallbackName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 6000);
  } catch (e) {
    toast(e.message || t("toast.error"), "err");
  }
}
async function previewDoc(path, title) {
  try {
    const { blob } = await apiBlob(path);
    const url = URL.createObjectURL(blob);
    openModal({
      title: title || (APP.lang === "ar" ? "معاينة المستند" : "Document preview"),
      wide: true,
      body: `<iframe class="pdf-frame" src="${url}" title="document"></iframe>`,
      footer: `<a class="btn btn-sm" href="${url}" download>⬇ ${ESC(APP.lang === "ar" ? "تنزيل" : "Download")}</a>
               <button class="btn btn-sm btn-primary" data-close>${ESC(t("common.close") || "Close")}</button>`,
      onClose: () => setTimeout(() => URL.revokeObjectURL(url), 1500),
    });
  } catch (e) { toast(e.message || t("toast.error"), "err"); }
}

/* ------------------------------ charts ------------------------------ */
function barChart(series, { height = 220 } = {}) {
  const w = 900, h = height, padL = 8, padB = 26, padT = 14;
  const max = Math.max(1, ...series.flatMap(s => [s.revenue_cents, s.expenses_cents]));
  const n = series.length;
  const slot = (w - padL * 2) / n;
  const bw = Math.min(28, slot / 3.2);
  const y = v => padT + (h - padT - padB) * (1 - v / max);
  let bars = "";
  series.forEach((s, i) => {
    const cx = padL + slot * i + slot / 2;
    const rh = (h - padT - padB) * (s.revenue_cents / max);
    const eh = (h - padT - padB) * (s.expenses_cents / max);
    bars += `<rect x="${cx - bw - 2}" y="${h - padB - rh}" width="${bw}" height="${Math.max(rh, 1)}" rx="4" fill="var(--brand)" opacity=".9"><title>${s.month}: ${moneyText(s.revenue_cents)}</title></rect>`;
    bars += `<rect x="${cx + 2}" y="${h - padB - eh}" width="${bw}" height="${Math.max(eh, 1)}" rx="4" fill="var(--accent)" opacity=".65"><title>${s.month}: ${moneyText(s.expenses_cents)}</title></rect>`;
    bars += `<text x="${cx}" y="${h - 8}" font-size="10.5" text-anchor="middle" fill="var(--text-3)">${s.month.slice(2)}</text>`;
  });
  const grid = [0.25, 0.5, 0.75, 1].map(f =>
    `<line x1="${padL}" x2="${w - padL}" y1="${y(max * f)}" y2="${y(max * f)}" stroke="var(--border)" stroke-dasharray="3 4"/>`).join("");
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
      ${grid}
      <text x="${padL}" y="10" font-size="10" fill="var(--text-3)">${moneyText(max)}</text>
      ${bars}
    </svg>`;
}

function doughnut(items, { size = 170 } = {}) {
  const total = items.reduce((a, b) => a + Math.max(0, b.value), 0) || 1;
  const colors = ["var(--brand)", "var(--accent)", "var(--warn)", "var(--ok)", "var(--danger)", "#8b5cf6"];
  let acc = 0;
  const r = 54, cx = 70, cy = 70, stroke = 20;
  const circumference = 2 * Math.PI * r;
  const arcs = items.map((it, i) => {
    const frac = Math.max(0, it.value) / total;
    const dash = frac * circumference;
    const el = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${colors[i % colors.length]}"
        stroke-width="${stroke}" stroke-dasharray="${dash} ${circumference - dash}"
        stroke-dashoffset="${-acc}" transform="rotate(-90 ${cx} ${cy})"><title>${ESC(it.label)}: ${moneyText(it.value)}</title></circle>`;
    acc += dash;
    return el;
  }).join("");
  const legend = items.map((it, i) =>
    `<div><i style="background:${colors[i % colors.length]}"></i>${ESC(it.label)} — ${fmtMoney(it.value)}</div>`).join("");
  return `<div style="display:flex;gap:20px;align-items:center;flex-wrap:wrap">
      <svg width="${size}" height="${size}" viewBox="0 0 140 140">${arcs}
        <text x="70" y="66" text-anchor="middle" font-size="10" fill="var(--text-3)">${ESC(t("common.total"))}</text>
        <text x="70" y="82" text-anchor="middle" font-size="12" font-weight="700" fill="var(--text)">${moneyText(total)}</text>
      </svg>
      <div class="legend" style="flex-direction:column;gap:6px">${legend}</div>
    </div>`;
}

/* ------------------------------ theme / language ------------------------------ */
function applyTheme() {
  const theme = APP.theme === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : APP.theme;
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("erp_theme", APP.theme);
  const btn = $("#theme-btn");
  if (btn) btn.textContent = theme === "dark" ? "☀️" : "🌙";
  const lbtn = $("#login-theme");
  if (lbtn) lbtn.textContent = theme === "dark" ? "☀️" : "🌙";
}
function applyLang() {
  document.documentElement.lang = APP.lang;
  document.documentElement.dir = APP.lang === "ar" ? "rtl" : "ltr";
  localStorage.setItem("erp_lang", APP.lang);
  $$("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-ph]").forEach(el => { el.placeholder = t(el.dataset.i18nPh); });
  const lb = $("#lang-btn"); if (lb) lb.textContent = APP.lang === "ar" ? "EN" : "ع";
  const ll = $("#login-lang"); if (ll) ll.textContent = APP.lang === "ar" ? "EN" : "ع";
}
async function setLang(lang) {
  APP.lang = lang;
  applyLang();
  if (APP.user) {
    try { await api("/api/auth/preferences", { method: "PATCH", body: { language: lang } }); } catch (e) { /* offline-safe */ }
  }
  render();
}
async function setTheme(theme) {
  APP.theme = theme;
  applyTheme();
  if (APP.user) {
    try { await api("/api/auth/preferences", { method: "PATCH", body: { theme } }); } catch (e) { /* offline-safe */ }
  }
}

/* ------------------------------ auth ------------------------------ */
async function login(username, password) {
  const res = await api("/api/auth/login", { method: "POST", body: { username, password } });
  APP.token = res.token;
  APP.user = res.user;
  localStorage.setItem("erp_token", res.token);
  APP.lang = res.user.language || APP.lang;
  APP.theme = res.user.theme || APP.theme;
  applyLang(); applyTheme();
  await bootApp();
}
function logoutLocal() {
  APP.token = null; APP.user = null;
  localStorage.removeItem("erp_token");
  $("#app").classList.add("hidden");
  $("#login-screen").classList.remove("hidden");
}
async function logout() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  logoutLocal();
}

/* ------------------------------ shell ------------------------------ */
const NAV = [
  { section: "nav.section.main" },
  { id: "dashboard", icon: "◈", label: "nav.dashboard", perm: "dashboard.view" },
  { section: "nav.accounting" },
  { id: "accounts", icon: "⛁", label: "nav.accounts", perm: "accounts.view" },
  { id: "journal", icon: "⇄", label: "nav.journal", perm: "journal.view" },
  { id: "reports", icon: "▤", label: "nav.reports", perm: "reports.view" },
  { section: "nav.operations" },
  { id: "sales", icon: "▦", label: "nav.sales", perm: "sales.view" },
  { id: "purchases", icon: "▧", label: "nav.purchases", perm: "purchases.view" },
  { id: "payments", icon: "₪", label: "nav.payments", perm: "payments.view" },
  { id: "payables", icon: "◫", label: "nav.payables", perm: "payables.view" },
  { id: "receivables", icon: "◨", label: "nav.receivables", perm: "customers.view" },
  { section: "nav.masters" },
  { id: "customers", icon: "☺", label: "nav.customers", perm: "customers.view" },
  { id: "vendors", icon: "⌂", label: "nav.vendors", perm: "vendors.view" },
  { id: "inventory", icon: "▩", label: "nav.inventory", perm: "inventory.view" },
  { id: "banks", icon: "▬", label: "nav.banks", perm: "banks.view" },
  { id: "assets", icon: "▣", label: "nav.assets", perm: "assets.view" },
  { id: "payroll", icon: "◍", label: "nav.payroll", perm: "payroll.view" },
  { id: "budgets", icon: "◑", label: "nav.budgets", perm: "budgets.view" },
  { section: "nav.admin" },
  { id: "settings", icon: "⚙", label: "nav.settings", perm: "settings.manage" },
  { id: "users", icon: "⚇", label: "nav.users", perm: "users.manage" },
  { id: "periods", icon: "▤", label: "nav.periods", perm: "periods.view" },
  { id: "audit", icon: "✎", label: "nav.audit", perm: "audit.view" },
  { id: "assistant", icon: "✦", label: "nav.assistant", perm: "ai.use" },
];

function canViewPeriods() { return can("reports.view") || can("period.close"); }

function renderNav() {
  const nav = $("#nav");
  const current = (location.hash.replace(/^#\/?/, "").split("?")[0].split("/")[0]) || "dashboard";
  nav.innerHTML = NAV.map(item => {
    if (item.section) {
      const key = item.section === "nav.section.main" ? "" : t(item.section);
      return key ? `<div class="nav-section-title">${ESC(key)}</div>` : "";
    }
    let allowed = !item.perm || can(item.perm);
    if (item.id === "periods") allowed = canViewPeriods();
    if (!allowed) return "";
    return `<button class="nav-item ${current === item.id ? "active" : ""}" data-route="${item.id}">
        <span class="ico">${item.icon}</span><span>${ESC(t(item.label))}</span></button>`;
  }).join("");
  $$(".nav-item", nav).forEach(b => b.onclick = () => { location.hash = "#/" + b.dataset.route; closeSidebar(); });
}

function renderShell() {
  const u = APP.user;
  const co = (APP.meta && APP.meta.companies && APP.meta.companies[0]) || null;
  $("#company-name").textContent = co ? (APP.lang === "ar" ? co.name_ar : co.name_en) : "";
  $("#userchip").innerHTML = `<div class="avatar">${ESC(initials(APP.lang === "ar" ? u.name_ar : u.name_en))}</div>
      <div><strong>${ESC(APP.lang === "ar" ? u.name_ar : u.name_en)}</strong>
      <small>${ESC(APP.lang === "ar" ? u.role_name_ar : u.role_name_en)}</small></div>`;
  $("#avatar-btn").textContent = initials(APP.lang === "ar" ? u.name_ar : u.name_en);
  $("#user-panel-head").innerHTML = `<strong>${ESC(APP.lang === "ar" ? u.name_ar : u.name_en)}</strong>
      <small>${ESC(u.username)} · ${ESC(APP.lang === "ar" ? u.role_name_ar : u.role_name_en)}</small>`;
  renderNav();
}

/* ------------------------------ notifications ------------------------------ */
async function loadNotifications() {
  try {
    const data = await api("/api/notifications");
    const unread = data.unread;
    const dot = $("#notif-count");
    dot.textContent = unread;
    dot.classList.toggle("hidden", !unread);
    const items = data.notifications.map(n => `
      <div class="notif-item ${n.is_read ? "" : "unread"}">
        ${n.is_read ? "" : '<div class="dot"></div>'}
        <div style="flex:1">
          <strong style="font-size:13px">${ESC(APP.lang === "ar" ? n.title_ar : n.title_en)}</strong>
          <div class="small muted">${ESC((APP.lang === "ar" ? n.body_ar : n.body_en) || "")}</div>
          <small>${ESC(fmtDate(n.created_at))}</small>
        </div>
      </div>`).join("") || `<div class="empty">${ESC(t("common.empty"))}</div>`;
    $("#notif-panel").innerHTML = `
      <div class="notif-head"><strong>${ESC(t("nav.dashboard") === "Dashboard" ? "Notifications" : "الإشعارات")}</strong>
        <button class="btn btn-sm btn-ghost" id="read-all">${ESC(APP.lang === "ar" ? "تعليم الكل كمقروء" : "Mark all read")}</button></div>
      ${items}`;
    const readAll = $("#read-all");
    if (readAll) readAll.onclick = async () => { await api("/api/notifications/read-all", { method: "POST" }); loadNotifications(); };
  } catch (e) { /* silently ignore */ }
}

/* ------------------------------ router ------------------------------ */
function parseRoute() {
  const raw = location.hash.replace(/^#\/?/, "") || "dashboard";
  const hash = raw.split("?")[0];
  const [name, ...rest] = hash.split("/");
  return { name, params: rest };
}

function setPageTitle(title, crumbs = []) {
  $("#page-title").textContent = title;
  $("#breadcrumbs").innerHTML = [t("app.name"), ...crumbs].map(ESC).join(" / ");
}

async function render() {
  const { name, params } = parseRoute();
  APP.route = { name, params };
  const view = $("#view");
  renderNav();
  renderBottomNav();
  const page = PAGES[name] || PAGES.dashboard;
  if (!can("platform.manage") && page.perm && !can(page.perm)) {
    view.innerHTML = `<div class="card"><div class="empty"><span class="big">🔒</span>
        ${ESC(t("error.forbidden"))}</div></div>`;
    return;
  }
  try {
    await page.render(view, params);
    if (window.wireExports) window.wireExports(view);
  } catch (e) {
    console.error(e);
    view.innerHTML = `<div class="card"><div class="empty"><span class="big">⚠️</span>
        ${ESC(e.message || t("toast.error"))}</div></div>`;
  }
}

/* ------------------------------ global search ------------------------------ */
function setupSearch() {
  const input = $("#global-search");
  const box = $("#search-results");
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { box.classList.add("hidden"); return; }
    timer = setTimeout(async () => {
      const items = [];
      try {
        const [je, cust, vend, sinv] = await Promise.all([
          can("journal.view") ? api("/api/journal-entries?q=" + encodeURIComponent(q) + "&per_page=5") : Promise.resolve({ entries: [] }),
          can("customers.view") ? api("/api/customers") : Promise.resolve({ customers: [] }),
          can("vendors.view") ? api("/api/vendors") : Promise.resolve({ vendors: [] }),
          can("sales.view") ? api("/api/sales-invoices") : Promise.resolve({ invoices: [] }),
        ]);
        je.entries.forEach(e => items.push({ icon: "⇄", title: `${e.entry_no} — ${e.memo}`, sub: fmtMoney(e.total_debit_cents), hash: "#/journal/" + e.id }));
        cust.customers.filter(c => (c.name_ar + c.name_en + c.code).toLowerCase().includes(q.toLowerCase()))
          .slice(0, 4).forEach(c => items.push({ icon: "☺", title: c.name_ar, sub: c.code, hash: "#/customers" }));
        vend.vendors.filter(c => (c.name_ar + c.name_en + c.code).toLowerCase().includes(q.toLowerCase()))
          .slice(0, 4).forEach(c => items.push({ icon: "⌂", title: c.name_ar, sub: c.code, hash: "#/vendors" }));
        sinv.invoices.filter(i => (i.invoice_no + (i.customer_ar || "")).toLowerCase().includes(q.toLowerCase()))
          .slice(0, 4).forEach(i => items.push({ icon: "▦", title: i.invoice_no + " — " + i.customer_ar, sub: fmtMoney(i.total_cents), hash: "#/sales/" + i.id }));
      } catch (e) { /* ignore */ }
      box.innerHTML = items.length
        ? items.map(i => `<div class="sr-item" data-hash="${ESC(i.hash)}"><span>${i.icon}</span>
            <div style="flex:1"><div>${ESC(i.title)}</div><small>${ESC(i.sub)}</small></div></div>`).join("")
        : `<div class="empty" style="padding:20px">${ESC(t("search.noResults"))}</div>`;
      box.classList.remove("hidden");
      $$(".sr-item", box).forEach(el => el.onclick = () => {
        location.hash = el.dataset.hash; box.classList.add("hidden"); input.value = "";
      });
    }, 220);
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".global-search")) box.classList.add("hidden");
  });
}

/* ------------------------------ quick create ------------------------------ */
function setupQuickCreate() {
  $("#quick-create").onclick = () => {
    const items = [];
    if (can("journal.create")) items.push(["⇄", t("dash.newEntry"), () => PAGES.journal.newEntry()]);
    if (can("sales.create")) items.push(["▦", t("dash.newInvoice"), () => PAGES.sales.newInvoice()]);
    if (can("payments.create")) items.push(["₪", t("dash.newReceipt"), () => PAGES.payments.newVoucher("receipt")]);
    if (can("purchases.create")) items.push(["▧", t("nav.purchases"), () => PAGES.purchases.newInvoice()]);
    openModal({
      title: t("dash.quickActions"), narrow: true,
      body: items.map((it, i) => `<button class="menu-item" data-i="${i}" style="padding:12px">
          <span style="margin-inline-end:8px">${it[0]}</span>${ESC(it[1])}</button>`).join(""),
      onMount: (root, close) => {
        $$("[data-i]", root).forEach(b => b.onclick = () => { close(); items[Number(b.dataset.i)][2](); });
      }
    });
  };
}

function setupCompanySwitcher() {
  const btn = $("#company-btn");
  const panel = $("#company-panel");
  if (!btn || !panel) return;
  const companies = (APP.meta && APP.meta.companies) || [];
  const branches = (APP.meta && APP.meta.branches) || [];
  const co = companies[0];
  const br = branches[0];
  const ar = APP.lang === "ar";
  const name = co ? (ar ? co.name_ar : co.name_en) : "—";
  $("#company-label").textContent = name;
  $("#branch-label").textContent = br ? (ar ? br.name_ar : br.name_en) : (ar ? "الفرع الرئيسي" : "Main branch");
  $("#company-initial").textContent = (name || "ط").slice(0, 1);
  panel.innerHTML = `
    <div class="menu-item" style="cursor:default;font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em">
      ${ESC(ar ? "الشركة" : "Company")}</div>
    ${companies.map(c => `<button class="menu-item" data-co="${c.id}">
        <strong>${ESC(ar ? c.name_ar : c.name_en)}</strong>
        <div class="small muted">${ESC(c.code)} · ${ESC(c.base_currency || "EGP")}</div></button>`).join("")}
    <div class="menu-item" style="cursor:default;font-size:11px;color:var(--text-3);text-transform:uppercase;letter-spacing:.06em;margin-top:6px">
      ${ESC(ar ? "الفرع" : "Branch")}</div>
    ${branches.map(b => `<button class="menu-item" data-br="${b.id}">
        ${ESC(ar ? b.name_ar : b.name_en)}</button>`).join("")}
    <div class="small muted" style="padding:8px 12px">${ESC(ar
      ? "تبديل الفرع يعيد تحميل التقارير لهذا الفرع."
      : "Switching the branch reloads reports for that branch.")}</div>`;
  btn.onclick = (e) => { e.stopPropagation(); panel.classList.toggle("hidden"); };
  $$("[data-co]", panel).forEach(b => b.onclick = () => {
    panel.classList.add("hidden");
    toast(APP.lang === "ar" ? "لديك صلاحية على هذه الشركة فقط في هذه النسخة." : "This build exposes a single company per user.", "ok");
  });
  $$("[data-br]", panel).forEach(b => b.onclick = () => {
    APP.branchId = Number(b.dataset.br);
    localStorage.setItem("erp_branch", APP.branchId);
    $("#branch-label").textContent = b.textContent.trim();
    panel.classList.add("hidden");
    toast(APP.lang === "ar" ? "تم تحديد الفرع: " + b.textContent.trim() : "Branch set: " + b.textContent.trim(), "ok");
    render();
  });
}

/* ------------------------------ mobile bottom nav ------------------------------ */
const BOTTOM_NAV = [
  { id: "dashboard", icon: "◈", label: "nav.dashboard", perm: "dashboard.view" },
  { id: "journal", icon: "⇄", label: "nav.journal", perm: "journal.view" },
  { id: "", icon: "＋", label: "", fab: true },
  { id: "sales", icon: "▦", label: "nav.sales", perm: "sales.view" },
  { id: "reports", icon: "▤", label: "nav.reports", perm: "reports.view" },
];
function renderBottomNav() {
  const el = $("#bottom-nav");
  if (!el) return;
  const current = (location.hash.replace(/^#\/?/, "").split("?")[0].split("/")[0]) || "dashboard";
  el.innerHTML = BOTTOM_NAV.filter(i => i.fab || !i.perm || can(i.perm)).map(i => {
    if (i.fab) return `<button class="fab" data-fab aria-label="quick create"><span class="ico">＋</span></button>`;
    return `<button data-route="${i.id}" class="${current === i.id ? "active" : ""}">
      <span class="ico">${i.icon}</span><span>${ESC(t(i.label))}</span></button>`;
  }).join("");
  $$("[data-route]", el).forEach(b => b.onclick = () => { location.hash = "#/" + b.dataset.route; });
  const fab = $("[data-fab]", el);
  if (fab) fab.onclick = () => { const q = $("#quick-create"); if (q) q.click(); };
}

/* ------------------------------ keyboard shortcuts ------------------------------ */
function setupShortcuts() {
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); const s = $("#global-search"); if (s) s.focus(); }
    if (e.key === "?" && !typing) { e.preventDefault(); shortcutsModal(); }
    if (e.key === "Escape") { closeSidebar(); $$(".dropdown-panel").forEach(p => p.classList.add("hidden")); }
  });
}
function shortcutsModal() {
  const rows = [
    ["/", APP.lang === "ar" ? "البحث الشامل" : "Global search"],
    ["?", APP.lang === "ar" ? "هذه القائمة" : "This help"],
    ["Esc", APP.lang === "ar" ? "إغلاق النوافذ والقوائم" : "Close panels"],
    ["Enter", APP.lang === "ar" ? "حفظ النموذج" : "Submit form"],
  ];
  openModal({
    title: APP.lang === "ar" ? "اختصارات لوحة المفاتيح" : "Keyboard shortcuts",
    narrow: true,
    body: rows.map(([k, d]) => `<div class="stat-line"><span>${ESC(d)}</span>
        <strong><code>${ESC(k)}</code></strong></div>`).join(""),
    footer: `<button class="btn btn-primary" data-close>${ESC(APP.lang === "ar" ? "تم" : "Got it")}</button>`,
  });
}

function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.add("hidden");
}

/* ------------------------------ demo accounts (login) ------------------------------ */
async function loadDemoAccounts() {
  const box = $("#demo-box");
  if (!box) return;
  try {
    const res = await fetch("/api/dev/accounts");
    if (!res.ok) return;                       // production / demo disabled
    const data = await res.json();
    if (!data.accounts || !data.accounts.length) return;
    $("#demo-grid").innerHTML = data.accounts.map((a, i) => `
      <button type="button" class="demo-row" data-i="${i}">
        <span class="dot" style="width:26px;height:26px;border-radius:9px;display:grid;place-items:center;
              background:linear-gradient(135deg,var(--brand),var(--brand-2));color:#fff;font-size:11px;font-weight:800">
          ${ESC((a.name_ar || a.username).slice(0, 1))}</span>
        <span class="who"><strong>${ESC(a.name_ar)}</strong><small>${ESC(a.role_name_ar || a.role)}</small></span>
        <code>${ESC(a.password)}</code>
      </button>`).join("");
    box.classList.remove("hidden");
    $$(".demo-row", box).forEach(b => b.onclick = () => {
      const acc = data.accounts[Number(b.dataset.i)];
      $("#login-username").value = acc.username;
      $("#login-password").value = acc.password;
      $("#login-password").focus();
      $("#login-form").requestSubmit ? $("#login-form").requestSubmit() : $("#login-form").submit();
    });
  } catch (e) { /* demo accounts are optional */ }
}

/* ------------------------------ boot ------------------------------ */
async function bootApp() {
  try {
    APP.meta = await api("/api/meta");
  } catch (e) {
    if (!APP.token) { logoutLocal(); return; }
  }
  const me = await api("/api/auth/me");
  APP.user = me.user;
  APP.lang = me.user.language || APP.lang;
  APP.theme = me.user.theme || APP.theme;
  applyLang(); applyTheme();
  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  renderShell();
  setupCompanySwitcher();
  renderBottomNav();
  setupSearch();
  setupQuickCreate();
  loadNotifications();
  if (!location.hash) location.hash = "#/dashboard";
  await render();
  if (APP.user.password_must_change) {
    setTimeout(() => toast(APP.lang === "ar" ? "يُفضّل تغيير كلمة المرور المؤقتة من قائمة الحساب." : "Please change your temporary password from the account menu.", "ok"), 1200);
  }
}

function setupGlobalHandlers() {
  $("#theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  $("#lang-btn").onclick = () => setLang(APP.lang === "ar" ? "en" : "ar");
  $("#login-theme").onclick = () => { APP.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; applyTheme(); };
  $("#login-lang").onclick = () => { APP.lang = APP.lang === "ar" ? "en" : "ar"; applyLang(); };
  $("#logout-btn").onclick = logout;
  $("#menu-logout").onclick = logout;
  $("#menu-password").onclick = () => changePasswordModal();
  $("#avatar-btn").onclick = (e) => { e.stopPropagation(); $("#user-panel").classList.toggle("hidden"); $("#notif-panel").classList.add("hidden"); };
  $("#notif-btn").onclick = (e) => { e.stopPropagation(); $("#notif-panel").classList.toggle("hidden"); $("#user-panel").classList.add("hidden"); loadNotifications(); };
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".dropdown")) { $$(".dropdown-panel").forEach(p => p.classList.add("hidden")); }
  });
  $("#sidebar-toggle").onclick = () => {
    const collapsed = $("#sidebar").classList.toggle("collapsed");
    localStorage.setItem("erp_sidebar", collapsed ? "collapsed" : "open");
  };
  if (localStorage.getItem("erp_sidebar") === "collapsed" && window.innerWidth > 900) {
    $("#sidebar").classList.add("collapsed");
  }
  $("#menu-btn").onclick = () => { $("#sidebar").classList.add("open"); $("#scrim").classList.remove("hidden"); };
  $("#scrim").onclick = closeSidebar;
  window.addEventListener("hashchange", () => render());

  const pwToggle = $("#pw-toggle");
  if (pwToggle) pwToggle.onclick = () => {
    const inp = $("#login-password");
    const show = inp.type === "password";
    inp.type = show ? "text" : "password";
    pwToggle.textContent = show ? "🙈" : "👁";
  };
  loadDemoAccounts();
  setupShortcuts();
  const ms = $("#mobile-search");
  if (ms) ms.onclick = () => {
    const box = $(".global-search");
    if (!box) return;
    box.classList.toggle("force-open");
    if (box.classList.contains("force-open")) { const i = $("#global-search"); if (i) i.focus(); }
  };

  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const err = $("#login-error");
    err.classList.add("hidden");
    try {
      await login($("#login-username").value.trim(), $("#login-password").value);
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.remove("hidden");
    }
  };
}

function changePasswordModal() {
  openForm({
    title: t("nav.changePassword"),
    fields: [
      { name: "current_password", label: t("login.password"), type: "password", required: true },
      { name: "new_password", label: t("users.password"), type: "password", required: true, hint: "≥ 10" },
    ],
    onSubmit: async (data) => {
      await api("/api/auth/change-password", { method: "POST", body: data });
      toast(t("common.saved"));
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  applyLang(); applyTheme(); setupGlobalHandlers();
  if (APP.token) {
    try { await bootApp(); } catch (e) { logoutLocal(); }
  } else {
    $("#login-screen").classList.remove("hidden");
    $("#app").classList.add("hidden");
  }
});
