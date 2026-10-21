/* ============================================================
   ELTARANISI ERP — front-end core (state, API, shell, router, UI kit)
   ============================================================ */
const APP = {
  token: localStorage.getItem("erp_token") || null,
  user: null,
  meta: null,
  lang: localStorage.getItem("erp_lang") || "ar",
  theme: localStorage.getItem("erp_theme_choice") || "dark",   // dark is the product default
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
/* Dates are stored as ISO (YYYY-MM-DD) everywhere in the API and the database.
   They are *shown* and *typed* as day/month/year: 08/12/2026 = 8 December 2026. */
function isoToDmy(iso) {
  if (!iso) return "";
  const m = String(iso).trim().slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
}
function dmyToIso(input) {
  if (input === null || input === undefined) return "";
  const s = String(input).trim().replace(/[.\-\s]/g, "/");
  if (!s) return "";
  let m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);          // d/m/Y  (the default)
  if (m) {
    const d = +m[1], mo = +m[2];
    if (mo >= 1 && mo <= 12 && d >= 1 && d <= 31)
      return `${m[3]}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    return "";
  }
  m = s.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);              // Y/m/d  (legacy / ISO)
  if (m) return `${m[1]}-${String(m[2]).padStart(2, "0")}-${String(m[3]).padStart(2, "0")}`;
  return "";
}
/** Accepts either 08/12/2026 or 2026-12-08 and always returns the ISO day. */
function dateVal(v) {
  if (!v) return "";
  const iso = dmyToIso(v);
  return iso || String(v).trim();
}
function fmtDate(d) {
  if (!d) return "—";
  return isoToDmy(String(d).slice(0, 10)) || String(d).slice(0, 10);
}

/* --- date inputs: typing mask, validation and a native calendar popup ------ */
(function wireDateInputs() {
  if (typeof document === "undefined" || !document.addEventListener) return;
  const guard = (t) => (t && t.matches && t.matches("input[data-date]")) ? t : null;

  document.addEventListener("input", (e) => {
    const el = guard(e.target);
    if (!el) return;
    const digits = el.value.replace(/\D/g, "").slice(0, 8);
    let out = digits;
    if (digits.length > 4) out = `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
    else if (digits.length > 2) out = `${digits.slice(0, 2)}/${digits.slice(2)}`;
    if (out !== el.value) el.value = out;
    el.classList.remove("invalid");
    el.removeAttribute("aria-invalid");
  });

  document.addEventListener("blur", (e) => {
    const el = guard(e.target);
    if (!el) return;
    const raw = el.value.trim();
    if (!raw) return;
    const iso = dmyToIso(raw);
    if (iso) {
      el.value = isoToDmy(iso);
      el.classList.remove("invalid");
      el.removeAttribute("aria-invalid");
    } else {
      el.classList.add("invalid");
      el.setAttribute("aria-invalid", "true");
    }
  }, true);

  let picker = null;
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest("[data-date-pick]") : null;
    if (!btn) return;
    e.preventDefault();
    const target = document.getElementById(btn.dataset.datePick);
    if (!target) return;
    if (!picker) {
      picker = document.createElement("input");
      picker.type = "date";
      picker.id = "native-date-picker";
      picker.style.cssText = "position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0";
      document.body.appendChild(picker);
    }
    picker.value = dateVal(target.value) || ((typeof today === "function") ? today() : "");
    picker.onchange = () => {
      if (picker.value) target.value = isoToDmy(picker.value);
      target.classList.remove("invalid");
      target.dispatchEvent(new Event("change", { bubbles: true }));
    };
    if (typeof picker.showPicker === "function") {
      try { picker.showPicker(); return; } catch (err) { /* fall through */ }
    }
    picker.click();
  });
})();

/** day/month/year text field with a calendar button (native picker on demand). */
function dateField(id, value, opts = {}) {
  const iso = dateVal(value || "");
  const attrs = [
    opts.name ? `name="${ESC(opts.name)}"` : "",
    opts.required ? "required" : "",
    opts.disabled ? "disabled" : "",
    opts.cls ? `class="${opts.cls}"` : "",
  ].filter(Boolean).join(" ");
  const label = (typeof L === "function")
    ? L("اختيار من التقويم", "Pick a date")
    : "Pick a date";
  return `<span class="date-field">
      <input id="${id}" type="text" inputmode="numeric" autocomplete="off" spellcheck="false"
             placeholder="يوم/شهر/سنة" value="${ESC(isoToDmy(iso))}" data-date ${attrs}/>
      <button type="button" class="date-pick" data-date-pick="${id}" aria-label="${ESC(label)}" title="${ESC(label)}">${icon("calendar")}</button>
    </span>`;
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
  div.innerHTML = `<span class="toast-ico">${kind === "err" ? icon("alert") : icon("check")}</span><div>${ESC(msg)}</div>`;
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
        <button class="icon-btn" data-close aria-label="close">${icon("close")}</button>
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

/* --------------------------------- editing --------------------------------
 * `editButton()` renders a pencil that opens the same validated form used for
 * creation, pre-filled with the current row. Submitting asks for confirmation
 * ("هل أنت متأكد من تعديل …؟") and then PATCHes the record; the server keeps its
 * own rules (drafts only, closed periods, unique codes) and audits both sides.
 * ------------------------------------------------------------------------ */
function editButton(path, subjectAr, subjectEn, fields) {
  const label = APP.lang === "ar" ? "تعديل" : "Edit";
  const payload = encodeURIComponent(JSON.stringify(fields || []));
  return `<button class="icon-btn edit" data-ed-path="${ESC(path)}"
      data-ed-ar="${ESC(subjectAr)}" data-ed-en="${ESC(subjectEn)}"
      data-ed-fields="${payload}" title="${ESC(label)}" aria-label="${ESC(label)}">${icon("edit")}</button>`;
}

/** Promise-based confirmation modal — resolves true only on an explicit "yes". */
function confirmAsync(message, { danger = true, title } = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (v) => { if (!settled) { settled = true; resolve(v); } };
    const ar = APP.lang === "ar";
    const { close } = openModal({
      title: title || (ar ? "تأكيد" : "Confirm"),
      narrow: true,
      body: `<p style="margin:0;font-size:14px;white-space:pre-line;line-height:1.7">${ESC(message)}</p>`,
      footer: `<button class="btn" data-cf-no>${ESC(t("common.cancel"))}</button>
               <button class="btn ${danger ? "btn-danger" : "btn-primary"}" data-cf-yes>
                 ${ESC(ar ? "تأكيد" : "Confirm")}</button>`,
      onMount: (root) => {
        root.querySelector("[data-cf-yes]").onclick = () => { finish(true); close(); };
        root.querySelector("[data-cf-no]").onclick = () => { finish(false); close(); };
      },
      onClose: () => finish(false),
    });
  });
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-ed-path]");
  if (!btn) return;
  ev.preventDefault();
  ev.stopPropagation();
  const ar = APP.lang === "ar";
  const subject = ar ? btn.dataset.edAr : btn.dataset.edEn;
  let fields = [];
  try { fields = JSON.parse(decodeURIComponent(btn.dataset.edFields || "%5B%5D")); } catch (e) { fields = []; }
  const baseFields = fields.map((f) => ({ ...f }));
  openEditForm(subject, fields);

  function openEditForm(subjectLabel, formFields) {
    openForm({
      title: (ar ? "تعديل " : "Edit ") + subjectLabel,
      fields: formFields,
      submitLabel: ar ? "حفظ التعديل" : "Save changes",
      wide: formFields.length > 6,
      onSubmit: async (data) => {
        const changed = Object.keys(data).some((k) => {
          const orig = baseFields.find((f) => f.name === k) || {};
          return String(data[k] ?? "") !== String(orig.value ?? "");
        });
        if (!changed) {
          toast(ar ? "لا يوجد تغيير لحفظه" : "Nothing changed", "err");
          return;
        }
        // the form modal closes right after this returns, so the confirmation
        // runs on the next tick and can restore the typed values if cancelled
        setTimeout(async () => {
          const ok = await confirmAsync(
            ar ? `هل أنت متأكد من تعديل ${subjectLabel}؟\nسيُسجَّل التعديل في سجل التدقيق باسمك ووقته.`
               : `Are you sure you want to edit ${subjectLabel}?\nThe change is recorded in the audit trail.`,
            { title: ar ? "تأكيد التعديل" : "Confirm edit" });
          if (!ok) {
            openEditForm(subjectLabel, baseFields.map((f) => ({ ...f, value: data[f.name] ?? f.value })));
            return;
          }
          btn.disabled = true;
          try {
            await api(btn.dataset.edPath, { method: "PATCH", body: data });
            toast(ar ? "تم حفظ التعديل" : "Changes saved");
            await render();
          } catch (err) {
            toast(err.message, "err");
            btn.disabled = false;
            openEditForm(subjectLabel, baseFields.map((f) => ({ ...f, value: data[f.name] ?? f.value })));
          }
        }, 40);
      },
    });
  }
});

/* ------------------------------- deletions --------------------------------
 * One button factory + one delegated handler for every "delete" action in the
 * app. The API additionally requires {"confirm": true} and re-checks
 * permissions server-side, so a forged click cannot delete anything.
 * ------------------------------------------------------------------------ */
function deleteButton(path, subjectAr, subjectEn, { cls = "danger", delBack = "" } = {}) {
  const label = APP.lang === "ar" ? "حذف" : "Delete";   // L() lives inside pages.js
  return `<button class="icon-btn ${cls}" data-del-path="${ESC(path)}"${delBack ? ` data-del-back="${ESC(delBack)}"` : ""}
      data-del-ar="${ESC(subjectAr)}" data-del-en="${ESC(subjectEn)}"
      title="${ESC(label)}" aria-label="${ESC(label)}">${icon("trash")}</button>`;
}

document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-del-path]");
  if (!btn) return;
  ev.preventDefault();
  ev.stopPropagation();
  const ar = APP.lang === "ar";
  const subject = ar ? btn.dataset.delAr : btn.dataset.delEn;
  const message = ar
    ? `هل أنت متأكد من حذف ${subject}؟\nسيتم حذف القيد المحاسبي المرتبط وتأثيره على الأرصدة، ولا يمكن التراجع عن هذه العملية.`
    : `Are you sure you want to delete ${subject}?\nIts journal entry and balance effects are removed too. This cannot be undone.`;
  confirmDialog(message, async () => {
    btn.disabled = true;
    try {
      await api(btn.dataset.delPath, { method: "DELETE", body: { confirm: true } });
      toast(ar ? "تم الحذف بنجاح" : "Deleted successfully");
      const back = btn.dataset.delBack;
      if (back) location.hash = back; else await render();
    } catch (err) {
      toast(err.message, "err");
    } finally {
      btn.disabled = false;
    }
  }, { danger: true, title: ar ? "تأكيد الحذف" : "Confirm deletion" });
});

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
          if (f.type === "date") {
            v = dateVal(v);
            if (f.required && !v) {
              showErr(root, `${f.label}: ${t("common.required")}`);
              el.focus();
              return;
            }
          }
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
  const common = `name="${f.name}" id="fld-${f.name}" ${f.required ? "required" : ""} ${f.readonly ? "readonly" : ""} ${f.disabled ? "disabled" : ""}`;
  let input;
  if (f.type === "select") {
    input = `<select ${common}>${(f.options || []).map(o =>
      `<option value="${ESC(o.value)}" ${String(o.value) === String(val) ? "selected" : ""}>${ESC(o.label)}</option>`).join("")}</select>`;
  } else if (f.type === "textarea") {
    input = `<textarea ${common} rows="${f.rows || 3}">${ESC(val)}</textarea>`;
  } else if (f.type === "checkbox") {
    input = `<label class="checkbox"><input type="checkbox" ${common} ${val ? "checked" : ""}/> ${ESC(f.label)}</label>`;
    return `<div class="field" style="grid-column:span 2">${input}${f.hint ? `<div class="field-hint">${ESC(f.hint)}</div>` : ""}</div>`;
  } else if (f.type === "date") {
    return `<div class="field" style="${f.span === 2 ? "grid-column:span 2" : ""}">
      <span>${ESC(f.label)}${f.required ? " *" : ""}</span>
      ${dateField(`fld-${f.name}`, val, { name: f.name, required: f.required, disabled: f.disabled })}
      ${f.hint ? `<div class="field-hint">${ESC(f.hint)}</div>` : ""}</div>`;
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
    return `<div class="empty"><span class="big">${icon("reports", "ico-lg")}</span>${ESC(opts.empty || t("common.empty"))}</div>`;
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
  const body = `<div>
        <div class="kpi-label">${ESC(label)}</div>
        <div class="kpi-value">${raw ? value : fmtMoney(value)}</div>
        ${sub ? `<div class="kpi-sub">${sub}</div>` : ""}
      </div>`;
  // Mobile: very narrow cards become a compact "label → value" row so the
  // numbers never get squeezed between the icon and the edge of the screen.
  if (icon === null || icon === undefined || icon === "") {
    return `<div class="card kpi kpi-compact ${tone}">${body}</div>`;
  }
  return `<div class="card kpi ${tone}">
      <div class="kpi-ico">${icon}</div>${body}
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
  // NOTE: no implicit persistence here — the stored value must mean
  // "the user picked this", otherwise the default could never change again.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#0b1020" : "#0b2f6b");
  const btn = $("#theme-btn");
  if (btn) btn.innerHTML = icon(theme === "dark" ? "sun" : "moon");
  const lbtn = $("#login-theme");
  if (lbtn) lbtn.innerHTML = icon(theme === "dark" ? "sun" : "moon");
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
  localStorage.setItem("erp_theme_choice", theme);   // explicit choice: remember it
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

/* ------------------------------------------------------------------
   Icon set — inline SVG (stroke icons, 24×24 grid).
   Emoji were removed: they render differently per OS and break the
   visual rhythm. These inherit `currentColor`, so they theme for free.
   ------------------------------------------------------------------ */
const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/><rect x="3" y="16" width="7" height="5" rx="2"/>',
  accounts: '<path d="M4 5.5C4 4.1 5.1 3 6.5 3H19a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H6.5A2.5 2.5 0 0 1 4 18.5z"/><path d="M4 17.5h16"/><path d="M9 7h7M9 11h5"/>',
  journal: '<path d="M4 7h13l-3-3M20 17H7l3 3"/>',
  reports: '<path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/><path d="M9 17v-4M12 17v-7M15 17v-2"/>',
  sales: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  purchases: '<circle cx="9" cy="20" r="1.6"/><circle cx="18" cy="20" r="1.6"/><path d="M2 3h2.5l2.2 11.2A2 2 0 0 0 8.7 16H19l2-9H6"/>',
  payments: '<rect x="2" y="6" width="20" height="12" rx="3"/><circle cx="12" cy="12" r="2.6"/><path d="M6 12h.01M18 12h.01"/>',
  debt: '<path d="M4 6h16"/><path d="M12 6v13"/><path d="M7 19h10"/><path d="M6 6l-3 6a3.2 3.2 0 0 0 6 0z"/><path d="M18 6l-3 6a3.2 3.2 0 0 0 6 0z"/><path d="M3.2 6h5.6M15.2 6h5.6"/>',
  payables: '<path d="M12 2v20"/><path d="M17 6.5C17 4.6 14.8 3.5 12 3.5S7 4.6 7 6.5s2.2 2.7 5 3.2 5 1.3 5 3.3-2.2 3.5-5 3.5-5-1.2-5-3"/>',
  receivables: '<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M17 8h5M19.5 5.5v5"/>',
  customers: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  vendors: '<path d="M2 17h2l1.5-4h11L18 17h2"/><rect x="5" y="7" width="11" height="6" rx="2"/><circle cx="7" cy="19" r="1.6"/><circle cx="17" cy="19" r="1.6"/>',
  inventory: '<path d="M3 8.5 12 4l9 4.5-9 4.5z"/><path d="M3 8.5v7L12 20l9-4.5v-7"/><path d="M12 13v7"/>',
  banks: '<path d="M3 10h18M5 10V6l7-3 7 3v4"/><path d="M6 10v7M10 10v7M14 10v7M18 10v7"/><path d="M3 21h18"/>',
  assets: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 7h6M9 11h6M9 15h4"/>',
  payroll: '<circle cx="9" cy="7" r="3"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 4h5v5"/>',
  budgets: '<circle cx="12" cy="12" r="9"/><path d="M12 3v9h9"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M17 11a3 3 0 1 0 0-6"/><path d="M18 20a5.5 5.5 0 0 0-3-4.9"/>',
  periods: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="M8 15h3"/>',
  audit: '<path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/><path d="M9 13l2 2 4-4"/>',
  assistant: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18 16l.9 2.1L21 19l-2.1.9L18 22l-.9-2.1L15 19l2.1-.9z"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  bell: '<path d="M18 15V10a6 6 0 1 0-12 0v5l-2 3h16z"/><path d="M10 21a2.5 2.5 0 0 0 4 0"/>',
  moon: '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4 8.5 8.5 0 1 0 20 14.5z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  trash: '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/><path d="M10 11v6M14 11v6"/>',
  edit: '<path d="M4 20h4.5L20 8.5a2.5 2.5 0 0 0-3.5-3.5L5 16.5z"/><path d="M14.5 6.5 18 10"/>',
  chevron: '<path d="M6 9l6 6 6-6"/>',
  check: '<path d="M20 6L9 17l-5-5"/>',
  alert: '<path d="M12 3l9 16H3z"/><path d="M12 9v4M12 17h.01"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-.6 4"/><path d="M20 5v6h-6"/>',
  grid: '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3.5 6h.01M3.5 12h.01M3.5 18h.01"/>',
  chart: '<path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6" rx="1"/><rect x="12" y="8" width="3" height="10" rx="1"/><rect x="17" y="5" width="3" height="13" rx="1"/>',
  pie: '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 2.5A8.5 8.5 0 0 1 21.5 9H15z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4"/>',
  percent: '<path d="M5 19L19 5"/><circle cx="7.5" cy="7.5" r="2.5"/><circle cx="16.5" cy="16.5" r="2.5"/>',
  transfer: '<path d="M4 8h13l-3.5-3.5M20 16H7l3.5 3.5"/>',
  coins: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  box: '<path d="M4 8l8-4 8 4v8l-8 4-8-4z"/><path d="M4 8l8 4 8-4M12 12v8"/>',
  arrowUp: '<path d="M12 20V4M6 10l6-6 6 6"/>',
  arrowDown: '<path d="M12 4v16M6 14l6 6 6-6"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M4 19h16"/>',
  file: '<path d="M7 3h7l4 4v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/>',
  eye: '<path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  printer: '<path d="M7 8V3h10v5"/><rect x="3" y="8" width="18" height="8" rx="2"/><path d="M7 14h10v7H7z"/>',
  upload: '<path d="M12 17V5M7 10l5-5 5 5"/><path d="M4 19h16"/>',
  undo: '<path d="M4 10h9a5.5 5.5 0 0 1 0 11H8"/><path d="M8 6l-4 4 4 4"/>',
  bank: '<path d="M3 9.5 12 4l9 5.5"/><path d="M5 10v8M10 10v8M14 10v8M19 10v8"/><path d="M3 21h18"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="M8 14h2M14 14h2M8 17.5h2M14 17.5h2"/>',
};

function icon(name, cls = "") {
  const path = ICONS[name] || ICONS.dashboard;
  return `<svg class="ico-svg ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}
window.icon = icon;

const NAV = [
  { section: "nav.section.main" },
  { id: "dashboard", icon: "dashboard", label: "nav.dashboard", perm: "dashboard.view" },
  { section: "nav.accounting" },
  { id: "accounts", icon: "accounts", label: "nav.accounts", perm: "accounts.view" },
  { id: "journal", icon: "journal", label: "nav.journal", perm: "journal.view" },
  { id: "reports", icon: "reports", label: "nav.reports", perm: "reports.view" },
  { section: "nav.operations" },
  { id: "sales", icon: "sales", label: "nav.sales", perm: "sales.view" },
  { id: "purchases", icon: "purchases", label: "nav.purchases", perm: "purchases.view" },
  { id: "payments", icon: "payments", label: "nav.payments", perm: "payments.view" },
  { id: "payables", icon: "payables", label: "nav.payables", perm: "payables.view" },
  { id: "debt", icon: "debt", label: "nav.debt", perm: "debt.view" },
  { id: "receivables", icon: "receivables", label: "nav.receivables", perm: "customers.view" },
  { section: "nav.masters" },
  { id: "customers", icon: "customers", label: "nav.customers", perm: "customers.view" },
  { id: "vendors", icon: "vendors", label: "nav.vendors", perm: "vendors.view" },
  { id: "inventory", icon: "inventory", label: "nav.inventory", perm: "inventory.view" },
  { id: "banks", icon: "banks", label: "nav.banks", perm: "banks.view" },
  { id: "assets", icon: "assets", label: "nav.assets", perm: "assets.view" },
  { id: "payroll", icon: "payroll", label: "nav.payroll", perm: "payroll.view" },
  { id: "budgets", icon: "budgets", label: "nav.budgets", perm: "budgets.view" },
  { section: "nav.admin" },
  { id: "settings", icon: "settings", label: "nav.settings", perm: "settings.manage" },
  { id: "users", icon: "users", label: "nav.users", perm: "users.manage" },
  { id: "periods", icon: "periods", label: "nav.periods", perm: "periods.view" },
  { id: "audit", icon: "audit", label: "nav.audit", perm: "audit.view" },
  { id: "assistant", icon: "assistant", label: "nav.assistant", perm: "ai.use" },
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
        <span class="ico">${icon(item.icon)}</span><span>${ESC(t(item.label))}</span></button>`;
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

/* --------------------------- storage safety --------------------------- */
/* A serverless deployment keeps its SQLite file in /tmp: the file is wiped when
   the instance is recycled, so anything typed in would be lost without a trace.
   The banner makes that impossible to miss and offers a full database backup. */
async function checkStorage() {
  try {
    const health = await api("/api/health");
    APP.storage = (health && health.storage) || { durable: true };
  } catch (e) {
    APP.storage = { durable: true };
    return;
  }
  const box = $("#storage-warning");
  if (!box) return;
  if (APP.storage.durable) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  const ar = APP.lang === "ar";
  const canBackup = typeof can === "function" && can("settings.manage");
  box.innerHTML = `<div class="storage-warning-inner">
      ${icon("alert")}
      <div>
        <strong>${ar ? "تنبيه: هذا النشر لا يحفظ البيانات بشكل دائم" : "Warning: this deployment does not persist data"}</strong>
        <div class="small">${ar
          ? "بيانات هذا الرابط مؤقتة على السيرفر وتُفقد عند إعادة النشر أو إعادة تشغيل النسخة. لعمل نظام دائم، اربط قاعدة بيانات مستمرة (انظر docs/deployment/PERSISTENCE.md)."
          : "Data on this URL is temporary and is lost when the deployment restarts. To keep it, connect a persistent database (see docs/deployment/PERSISTENCE.md)."}</div>
      </div>
      ${canBackup ? `<button class="btn btn-sm" id="storage-backup">${icon("download")} ${ar ? "نسخة احتياطية" : "Backup"}</button>` : ""}
    </div>`;
  box.classList.remove("hidden");
  const btn = $("#storage-backup");
  if (btn) btn.onclick = () => downloadApi("/api/admin/backup", "eltaranisi-erp-backup.db");
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
        je.entries.forEach(e => items.push({ icon: icon("journal"), title: `${e.entry_no} — ${e.memo}`, sub: fmtMoney(e.total_debit_cents), hash: "#/journal/" + e.id }));
        cust.customers.filter(c => (c.name_ar + c.name_en + c.code).toLowerCase().includes(q.toLowerCase()))
          .slice(0, 4).forEach(c => items.push({ icon: icon("customers"), title: c.name_ar, sub: c.code, hash: "#/customers" }));
        vend.vendors.filter(c => (c.name_ar + c.name_en + c.code).toLowerCase().includes(q.toLowerCase()))
          .slice(0, 4).forEach(c => items.push({ icon: icon("vendors"), title: c.name_ar, sub: c.code, hash: "#/vendors" }));
        sinv.invoices.filter(i => (i.invoice_no + (i.customer_ar || "")).toLowerCase().includes(q.toLowerCase()))
          .slice(0, 4).forEach(i => items.push({ icon: icon("sales"), title: i.invoice_no + " — " + i.customer_ar, sub: fmtMoney(i.total_cents), hash: "#/sales/" + i.id }));
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
    if (can("journal.create")) items.push([icon("journal"), t("dash.newEntry"), () => PAGES.journal.newEntry()]);
    if (can("sales.create")) items.push([icon("sales"), t("dash.newInvoice"), () => PAGES.sales.newInvoice()]);
    if (can("payments.create")) items.push([icon("coins"), t("dash.newReceipt"), () => PAGES.payments.newVoucher("receipt")]);
    if (can("purchases.create")) items.push([icon("purchases"), t("nav.purchases"), () => PAGES.purchases.newInvoice()]);
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
  { id: "dashboard", icon: "dashboard", label: "nav.dashboard", perm: "dashboard.view" },
  { id: "journal", icon: "journal", label: "nav.journal", perm: "journal.view" },
  { id: "", icon: "plus", label: "", fab: true },
  { id: "sales", icon: "sales", label: "nav.sales", perm: "sales.view" },
  { id: "reports", icon: "reports", label: "nav.reports", perm: "reports.view" },
];
function renderBottomNav() {
  const el = $("#bottom-nav");
  if (!el) return;
  const current = (location.hash.replace(/^#\/?/, "").split("?")[0].split("/")[0]) || "dashboard";
  el.innerHTML = BOTTOM_NAV.filter(i => i.fab || !i.perm || can(i.perm)).map(i => {
    if (i.fab) return `<button class="fab" data-fab aria-label="quick create"><span class="ico">${icon("plus")}</span></button>`;
    return `<button data-route="${i.id}" class="${current === i.id ? "active" : ""}">
      <span class="ico">${icon(i.icon)}</span><span>${ESC(t(i.label))}</span></button>`;
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
  // Hosted/production deployments are not a "demo environment": drop the labels
  // that claim otherwise instead of showing them to real users.
  try {
    const h = await fetch("/api/health").then(r => r.json()).catch(() => ({}));
    if (h && h.dev_mode === false) {
      document.body.classList.add("is-production");
      const foot = document.querySelector(".login-foot");
      if (foot) foot.remove();
      const hint = document.querySelector(".login-hint [data-i18n], .login-hint span");
      if (hint) {
        hint.dataset.i18n = "login.hintProd";     // survives language switches
        hint.textContent = t("login.hintProd");
      }
    }
  } catch (e) { /* offline-safe */ }
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
  await checkStorage();
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
  $("#login-theme").onclick = () => {
    APP.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    localStorage.setItem("erp_theme_choice", APP.theme);
    applyTheme();
  };
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
