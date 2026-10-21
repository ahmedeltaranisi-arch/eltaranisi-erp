/* ============================================================
   ELTARANISI ERP — pages
   ============================================================ */
(function () {
  const today = () => (APP.meta && APP.meta.today) || new Date().toISOString().slice(0, 10);
  const yearStart = () => today().slice(0, 4) + "-01-01";
  const L = (ar, en) => (APP.lang === "ar" ? ar : en);

  function pageHead(titleKey, subKey, actions = "") {
    return `<div class="page-head">
      <div><h1>${ESC(t(titleKey))}</h1><p>${ESC(t(subKey))}</p></div>
      <div class="actions">${actions}</div></div>`;
  }
  function setTitle(titleKey, crumbs = []) { setPageTitle(t(titleKey), crumbs); }

  function optionsFrom(rows, valueKey, labelKey = null) {
    return rows.map(r => ({ value: r[valueKey], label: labelKey ? r[labelKey] : (APP.lang === "ar" ? r.name_ar : r.name_en) }));
  }
  function exportBtn(kind, params = {}) {
    const q = new URLSearchParams({ kind, ...params }).toString();
    return `<button class="btn btn-sm" data-export="/api/reports/export?${q}" data-name="${kind}.csv">${icon("download")} ${ESC(t("common.export"))}</button>`;
  }

  /** Export menu for a report: CSV / Excel / PDF (AR & EN) — all via authenticated fetch. */
  function reportExports(kind, params = {}) {
    const q = (extra) => new URLSearchParams({ kind, ...params, ...extra }).toString();
    const pairs = [
      ["pdf", "ar", L("عربي", "Arabic"), "📄", "PDF · A4"],
      ["pdf", "en", L("إنجليزي", "English"), "📄", "PDF · A4"],
      ["xlsx", "ar", L("Excel", "Excel"), icon("chart"), "XLSX · RTL"],
      ["csv", "ar", L("CSV", "CSV"), icon("file"), "CSV · UTF-8"],
    ];
    return `<details class="export-menu"><summary>${icon("download")} ${ESC(L("تصدير", "Export"))} <span class="caret">${icon("chevron")}</span></summary>
      <div class="menu">
        ${pairs.map(([fmt, lang, label, icon, hint]) => {
          const url = fmt === "csv" ? `/api/reports/export?${q({})}` : `/api/reports/${fmt}?${q({ lang })}`;
          return `<button data-export="${url}" data-name="${kind}.${fmt}"><span class="mi">${icon}</span>
            <span class="ml">${ESC(label)}</span><span class="hint">${hint}</span></button>`;
        }).join("")}
      </div></details>`;
  }

  /** Document toolbar for an invoice: print, PDF (AR/EN), Excel, e-invoice JSON. */
  function docToolbar(kind, id) {
    const base = kind === "sales" ? "sales-invoices" : "purchase-invoices";
    return `<div class="doc-toolbar">
      <details class="export-menu"><summary>${icon("file")} ${ESC(L("PDF / طباعة", "PDF / print"))} <span class="caret">${icon("chevron")}</span></summary>
        <div class="menu">
          <button data-preview="/api/${base}/${id}/pdf?lang=${APP.lang}">${icon("eye")} ${ESC(L("معاينة وطباعة", "Preview & print"))}</button>
          <button data-export="/api/${base}/${id}/pdf?lang=ar" data-name="invoice-ar.pdf">${icon("file")} ${ESC(L("تنزيل PDF عربي", "Arabic PDF"))}</button>
          <button data-export="/api/${base}/${id}/pdf?lang=en" data-name="invoice-en.pdf">${icon("file")} ${ESC(L("تنزيل PDF إنجليزي", "English PDF"))}</button>
        </div></details>
      <button class="btn btn-sm" data-export="/api/${base}/${id}/xlsx?lang=${APP.lang}" data-name="invoice.xlsx">${icon("chart")} Excel</button>
      <button class="btn btn-sm" data-einv="/api/${base}/${id}/einvoice">${icon("bank")} ${ESC(L("فاتورة إلكترونية", "E-invoice"))}</button>
    </div>`;
  }

  /** Wire every [data-export] / [data-preview] / [data-einv] inside a container. */
  function wireExports(root) {
    $$("[data-export]", root).forEach(b => b.onclick = (e) => {
      e.preventDefault();
      const det = b.closest("details");
      if (det) det.open = false;
      downloadApi(b.dataset.export, b.dataset.name || "export");
    });
    $$("[data-preview]", root).forEach(b => b.onclick = (e) => {
      e.preventDefault();
      const det = b.closest("details");
      if (det) det.open = false;
      previewDoc(b.dataset.preview, L("معاينة المستند", "Document preview"));
    });
    $$("[data-einv]", root).forEach(b => b.onclick = async () => {
      try {
        const d = await api(b.dataset.einv);
        openModal({
          title: L("جاهزية الفاتورة الإلكترونية", "E-invoicing readiness"),
          wide: true,
          body: `<div class="form-error" style="background:color-mix(in srgb,var(--warn) 12%,transparent);color:var(--warn);border-color:color-mix(in srgb,var(--warn) 30%,transparent)">
              ${ESC(L(I18N_EINV_NOTE_AR, I18N_EINV_NOTE_EN))}</div>
            <div class="stat-line"><span>UUID</span><strong class="mono" style="direction:ltr">${ESC(d.documentUuid)}</strong></div>
            <div class="stat-line"><span>SHA-256</span><strong class="mono" style="direction:ltr;font-size:11px">${ESC(d.digest.replace("sha256:", ""))}</strong></div>
            <div class="stat-line"><span>${ESC(t("common.total"))}</span><strong>${fmtMoney(Math.round((d.document.totalAmount || 0) * 100))}</strong></div>
            <pre dir="ltr" style="max-height:300px;overflow:auto;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:12px;font-size:11.5px">${ESC(JSON.stringify(d, null, 2))}</pre>`,
          footer: `<button class="btn btn-sm" id="einv-copy">${ESC(L("نسخ JSON", "Copy JSON"))}</button>
                   <button class="btn btn-sm btn-primary" data-close>${ESC(t("common.close") || "OK")}</button>`,
          onMount: (r2) => {
            const cp = $("#einv-copy", r2);
            if (cp) cp.onclick = () => {
              navigator.clipboard?.writeText(JSON.stringify(d, null, 2));
              toast(L("تم النسخ", "Copied"), "ok");
            };
          }
        });
      } catch (e) { toast(e.message, "err"); }
    });
  }
  window.wireExports = wireExports;   // let the router wire exports on every page
  const I18N_EINV_NOTE_AR = "مستند جاهزية غير موقّع — الإرسال إلى مصلحة الضرائب يتطلب تكاملًا معتمدًا وشهادة ختم إلكتروني. لم يتم إرسال أي بيانات خارج النظام.";
  const I18N_EINV_NOTE_EN = "Unsigned readiness document — transmission to the tax authority requires an accredited integration and e-seal certificate. No data has been sent outside the system.";
  function printBtn() {
    return `<button class="btn btn-sm" onclick="window.print()">${icon("printer")} ${ESC(t("common.print"))}</button>`;
  }

  /* ==================================================================
     COMPANY SETTINGS (invoice identity)
     ================================================================== */
  PAGES.settings = {
    perm: "reports.view",
    async render(view) {
      setTitle("nav.settings");
      const d = await api("/api/company");
      const co = d.company || {}, st = d.settings || {};   // never let a bad payload blank the page
      const ro = d.can_edit ? "" : "disabled";
      const field = (id, label, value, type = "text") =>
        `<div class="field"><span>${ESC(label)}</span>
           <input id="${id}" type="${type}" value="${ESC(value || "")}" ${ro}/></div>`;
      view.innerHTML = `${pageHead(L("إعدادات الشركة والفواتير", "Company & invoice settings"), "settings.subtitle")}
        <div class="split">
          ${card(L("بيانات الشركة", "Company profile"), `
            ${field("co-name-ar", L("الاسم بالعربية", "Arabic name"), co.name_ar)}
            ${field("co-name-en", L("الاسم بالإنجليزية", "English name"), co.name_en)}
            <div class="grid cols-2" style="gap:0 14px">
              ${field("co-tax", L("الرقم الضريبي", "Tax number"), co.tax_number)}
              ${field("co-phone", L("الهاتف", "Phone"), co.phone)}
            </div>
            ${field("co-address", L("العنوان", "Address"), co.address)}
            <div class="grid cols-2" style="gap:0 14px">
              ${field("co-email", L("البريد الإلكتروني", "Email"), st.company_email, "email")}
              ${field("co-website", L("الموقع", "Website"), st.company_website)}
            </div>`)}
          ${card(L("بيانات الفاتورة", "Invoice defaults"), `
            ${field("co-bank", L("بيانات البنك (تظهر أسفل الفاتورة)", "Bank details (invoice footer)"), st.bank_details)}
            ${field("co-terms", L("شروط السداد", "Payment terms"), st.invoice_terms)}
            ${field("co-footer", L("نص تذييل الفاتورة", "Invoice footer text"), st.invoice_footer)}
            <div class="doc-toolbar" style="margin-top:6px">
              ${d.can_edit ? `<button class="btn btn-primary" id="co-save">${ESC(t("common.save"))}</button>` : ""}
              <button class="btn" data-export="/api/reports/pdf?kind=vendor-payables" data-name="sample.pdf">${icon("file")} ${ESC(L("معاينة ترويسة التقارير", "Preview report header"))}</button>
            </div>
            <p class="field-hint" style="margin-top:10px">${ESC(L(
              "هذه البيانات تُستخدم في رأس الفاتورة الضريبية وملفات PDF/Excel وفي مستند الجاتوزية.",
              "These values appear on tax invoices, PDF/Excel exports and the e-invoice document."))}</p>`)}
        </div>
        ${card(L("معاينة رأس الفاتورة", "Invoice header preview"), `
          <div style="border:1px solid var(--border);border-radius:14px;overflow:hidden">
            <div class="inv-preview-head">
              <span class="inv-preview-mark">ط</span>
              <div class="inv-preview-id">
                <strong id="pv-name">${ESC(co.name_ar)}</strong>
                <small id="pv-sub">${ESC(co.name_en)}</small>
              </div>
              <div class="inv-preview-meta">
                <div>${ESC(L("الرقم الضريبي", "Tax No."))}: <span id="pv-tax" dir="ltr">${ESC(co.tax_number || "—")}</span></div>
                <div id="pv-addr">${ESC(co.address || "")}</div>
              </div>
            </div>
            <div style="padding:14px 20px;background:var(--surface-2);font-size:12.5px;color:var(--text-2)">
              ${ESC(L("فاتورة ضريبية — الترويسة أعلاه تُطبع على كل فاتورة PDF.", "Tax invoice — the header above is printed on every PDF invoice."))}
            </div>
          </div>`)}
      `;
      wireExports(view);
      const save = $("#co-save", view);
      if (save) save.onclick = async () => {
        try {
          await api("/api/company", { method: "PATCH", body: {
            name_ar: $("#co-name-ar").value, name_en: $("#co-name-en").value,
            tax_number: $("#co-tax").value, phone: $("#co-phone").value,
            address: $("#co-address").value,
            company_email: $("#co-email").value, company_website: $("#co-website").value,
            bank_details: $("#co-bank").value, invoice_terms: $("#co-terms").value,
            invoice_footer: $("#co-footer").value,
          }});
          toast(t("common.saved"), "ok");
          render();
        } catch (e) { toast(e.message, "err"); }
      };
      // live header preview
      const bind = (id, target, fallback = "") => {
        const el = $(id, view);
        if (el) el.oninput = () => { const t2 = $(target, view); if (t2) t2.textContent = el.value || fallback; };
      };
      bind("#co-name-ar", "#pv-name");
      bind("#co-name-en", "#pv-sub");
      bind("#co-tax", "#pv-tax", "—");
      bind("#co-address", "#pv-addr");
    }
  };

  /* ==================================================================
     DASHBOARD
     ================================================================== */
  PAGES.dashboard = {
    perm: "dashboard.view",
    async render(view) {
      setTitle("nav.dashboard");
      view.innerHTML = `<div class="skeleton-wrap"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>`;
      const d = await api("/api/dashboard");
      const k = d.kpis;
      const main = `
        ${pageHead("dash.title", "dash.subtitle", `
          <span class="chip">${ESC(t("common.asOf"))}: ${fmtDate(d.as_of)}</span>
          ${can("assistant") || can("ai.use") ? `<a class="btn btn-sm" href="#/assistant">${icon("sparkle")} ${ESC(t("nav.assistant"))}</a>` : ""}
          <a class="btn btn-sm" href="#/reports">${icon("reports")} ${ESC(t("nav.reports"))}</a>`)}

        <div class="grid cols-4">
          ${kpi({ label: t("dash.revenue"), value: k.revenue_cents, icon: icon("arrowUp"), tone: "ok" })}
          ${kpi({ label: t("dash.expenses"), value: k.expenses_cents, icon: icon("arrowDown"), tone: "danger" })}
          ${kpi({ label: t("dash.netProfit"), value: k.net_profit_cents, icon: icon("assets"), tone: k.net_profit_cents >= 0 ? "ok" : "danger",
                  sub: `${ESC(t("common.total"))}: ${pct(k.net_profit_cents, k.revenue_cents || 1)}` })}
          ${kpi({ label: t("dash.cash"), value: k.cash_cents, icon: icon("banks") })}
        </div>
        <div class="grid cols-4" style="margin-top:16px">
          ${kpi({ label: t("dash.ap"), value: k.ap_cents, icon: icon("reports"), tone: "warn",
                  sub: `${ESC(t("payables.overdue"))}: ${fmtMoney(k.vendor_overdue_cents)}` })}
          ${kpi({ label: t("dash.ar"), value: k.ar_cents, icon: icon("chart"),
                  sub: `${ESC(t("dash.overdue"))}: ${k.overdue_count}` })}
          ${kpi({ label: t("dash.inventory"), value: k.inventory_value_cents, icon: icon("grid") })}
          ${kpi({ label: t("dash.pending"), value: k.pending_approvals, icon: icon("pie"), raw: true,
                  sub: `<a href="#/journal?status=review">${ESC(t("common.viewAll"))}</a>` })}
        </div>

        <div class="split" style="margin-top:18px">
          ${card(t("dash.series"), `
            ${barChart(d.series)}
            <div class="legend" style="margin-top:8px">
              <div><i style="background:var(--brand)"></i>${ESC(t("dash.revenue"))}</div>
              <div><i style="background:var(--accent)"></i>${ESC(t("dash.expenses"))}</div>
            </div>`, "", "")}
          ${card(t("dash.cashAccounts"), `
            ${d.cash_accounts.map(a => `<div class="stat-line"><span>${ESC(APP.lang === "ar" ? a.name_ar : (a.name_en || a.name_ar))}<br><small class="muted">${ESC(a.gl_code)}</small></span>
                <strong>${fmtMoney(a.balance_cents)}</strong></div>`).join("")}
            <div class="stat-line" style="border-top:2px solid var(--border-strong);margin-top:6px">
              <span><strong>${ESC(t("common.total"))}</strong></span><strong>${fmtMoney(k.cash_cents)}</strong></div>`)}
        </div>

        <div class="split" style="margin-top:18px">
          ${card(t("dash.topVendors"), renderTable([
            { key: "name_ar", label: t("common.vendor"), cls: "wrap", render: r => `<div>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}<br><small class="muted">${ESC(r.code)}</small></div>` },
            { key: "outstanding_cents", label: t("payables.outstanding"), cls: "num", render: r => fmtMoney(r.outstanding_cents) },
            { key: "overdue_cents", label: t("payables.overdue"), cls: "num", render: r => r.overdue_cents ? `<span class="neg">${fmtMoney(r.overdue_cents)}</span>` : "—" },
          ], d.top_vendors, { compact: true }), `<a class="btn btn-sm btn-ghost" href="#/payables">${ESC(t("common.viewAll"))}</a>`)}
          ${card(t("dash.recent"), renderTable([
            { key: "entry_no", label: L("الرمز التعريفي", "Reference ID"), render: r => `<a href="#/journal/${r.id}">${ESC(r.entry_no)}</a>` },
            { key: "entry_date", label: t("common.date"), render: r => fmtDate(r.entry_date) },
            { key: "memo", label: t("common.description"), cls: "wrap", render: r => `<span title="${ESC(r.memo)}">${ESC(r.memo.slice(0, 34))}${r.memo.length > 34 ? "…" : ""}</span>` },
            { key: "total_debit_cents", label: t("common.amount"), cls: "num", render: r => fmtMoney(r.total_debit_cents) },
            { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          ], d.recent_entries, { compact: true }), `<a class="btn btn-sm btn-ghost" href="#/journal">${ESC(t("common.viewAll"))}</a>`)}
        </div>

        <div class="split" style="margin-top:18px">
          ${card(t("dash.arAging"), doughnut([
            { label: t("reports.bucket.current"), value: d.ar_aging.current },
            { label: t("reports.bucket.1_30"), value: d.ar_aging["1_30"] },
            { label: t("reports.bucket.31_60"), value: d.ar_aging["31_60"] },
            { label: t("reports.bucket.61_90"), value: d.ar_aging["61_90"] },
            { label: t("reports.bucket.over_90"), value: d.ar_aging.over_90 },
          ]))}
          ${card(t("dash.apAging"), doughnut([
            { label: t("reports.bucket.current"), value: d.ap_aging.current },
            { label: t("reports.bucket.1_30"), value: d.ap_aging["1_30"] },
            { label: t("reports.bucket.31_60"), value: d.ap_aging["31_60"] },
            { label: t("reports.bucket.61_90"), value: d.ap_aging["61_90"] },
            { label: t("reports.bucket.over_90"), value: d.ap_aging.over_90 },
          ]))}
        </div>`;
      view.innerHTML = main;
    }
  };

  /* ==================================================================
     CHART OF ACCOUNTS
     ================================================================== */
  PAGES.accounts = {
    perm: "accounts.view",
    async render(view) {
      setTitle("nav.accounts");
      const data = await api("/api/accounts");
      const typeNames = { asset: "accounts.asset", liability: "accounts.liability", equity: "accounts.equity", revenue: "accounts.revenue", expense: "accounts.expense" };
      const actions = `${can("accounts.create") ? `<button class="btn btn-primary btn-sm" id="new-acc">${icon("plus")} ${ESC(t("accounts.new"))}</button>` : ""}`;
      const groups = {};
      data.accounts.forEach(a => { (groups[a.type] = groups[a.type] || []).push(a); });

      const tree = Object.keys(typeNames).map(type => {
        const rows = groups[type] || [];
        if (!rows.length) return "";
        const total = rows.filter(r => r.is_postable).reduce((s, r) => s + r.display_balance_cents, 0);
        return `<div class="section-title"><span>${ESC(t(typeNames[type]))}</span><span class="line"></span>
            <span class="mono">${fmtMoney(total)}</span></div>
          ${rows.map(a => `
            <div class="tree-row ${a.is_postable ? "postable" : ""}" style="padding-inline-start:${12 + (a.level - 1) * 18}px"
                 ${a.is_postable ? `data-acc="${a.id}"` : ""}>
              <span class="code">${ESC(a.code)}</span>
              <span class="name ${a.is_postable ? "" : "muted"}">${ESC(APP.lang === "ar" ? a.name_ar : a.name_en)}
                ${a.is_postable ? "" : `<span class="pill">${ESC(L("تجميعي", "group"))}</span>`}
                ${a.is_active ? "" : `<span class="badge red">${ESC(t("status.inactive"))}</span>`}</span>
              <span class="bal">${fmtMoney(a.display_balance_cents)}</span>
            </div>`).join("")}`;
      }).join("");

      view.innerHTML = `${pageHead("accounts.title", "accounts.subtitle", actions)}
        ${card("", `
          <div class="table-toolbar"><input id="acc-search" placeholder="${ESC(t("common.search"))}" style="flex:1;max-width:320px"/>
            <span class="muted small">${data.accounts.length} ${ESC(t("common.rows"))}</span></div>
          <div id="acc-tree" style="padding:8px 10px 16px">${tree}</div>`)}`;

      const wire = (root) => {
        root.querySelectorAll("[data-acc]").forEach(el => el.onclick = () => accountsLedgerModal(Number(el.dataset.acc), data.accounts.find(a => a.id === Number(el.dataset.acc))));
      };
      wire(view);
      $("#acc-search").oninput = (e) => {
        const q = e.target.value.toLowerCase();
        $$("#acc-tree .tree-row").forEach(r => {
          r.style.display = r.textContent.toLowerCase().includes(q) ? "" : "none";
        });
      };
      if (can("accounts.create")) {
        $("#new-acc").onclick = () => openForm({
          title: t("accounts.new"),
          fields: [
            { name: "code", label: t("common.code"), required: true },
            { name: "type", label: t("accounts.type"), type: "select", required: true,
              options: Object.keys(typeNames).map(k => ({ value: k, label: t(typeNames[k]) })) },
            { name: "name_ar", label: "الاسم بالعربية", required: true },
            { name: "name_en", label: "Name (English)", required: true },
            { name: "parent_id", label: t("accounts.parent"), type: "select",
              options: [{ value: "", label: "—" }, ...data.accounts.map(a => ({ value: a.id, label: `${a.code} — ${a.name_ar}` }))] },
          ],
          onSubmit: async (d) => {
            await api("/api/accounts", { method: "POST", body: { ...d, parent_id: d.parent_id || null } });
            toast(t("common.saved")); render();
          }
        });
      }
    }
  };

  async function accountsLedgerModal(accountId, acc) {
    const to = today(), from = yearStart();
    const data = await api(`/api/reports/ledger?account_id=${accountId}&from=${from}&to=${to}`);
    openModal({
      title: `${acc.code} — ${APP.lang === "ar" ? acc.name_ar : acc.name_en}`,
      wide: true,
      body: `<div class="table-toolbar" style="border:1px solid var(--border);border-radius:12px;margin-bottom:12px">
          <span class="muted small">${from} → ${to}</span><div style="flex:1"></div>
          ${exportBtn("journal", { from, to })}</div>
        ${renderTable([
          { key: "entry_date", label: t("common.date") },
          { key: "entry_no", label: L("الرمز التعريفي", "Reference ID") },
          { key: "description", label: t("common.description") },
          { key: "debit_cents", label: t("common.debit"), cls: "num", render: r => r.debit_cents ? fmtMoney(r.debit_cents) : "" },
          { key: "credit_cents", label: t("common.credit"), cls: "num", render: r => r.credit_cents ? fmtMoney(r.credit_cents) : "" },
          { key: "running_cents", label: t("common.balance"), cls: "num", render: r => fmtMoney(r.running_cents) },
        ], data.rows, {
          footer: `<tr><td colspan="3">${ESC(t("reports.closing"))}</td>
            <td class="num"></td><td class="num"></td><td class="num">${fmtMoney(data.closing_cents)}</td></tr>`
        })}`
    });
  }

  /* ==================================================================
     JOURNAL
     ================================================================== */
  const STATUS_FILTERS = ["all", "draft", "review", "approved", "posted", "reversed"];

  PAGES.journal = {
    perm: "journal.view",
    async render(view, params) {
      if (params && params[0]) return PAGES.journal.detail(view, params[0]);
      setTitle("nav.journal");
      const state = { status: new URLSearchParams((location.hash.split("?")[1] || "")).get("status") || "all", q: "", from: "", to: "" };
      view.innerHTML = `${pageHead("journal.title", "journal.subtitle",
        can("journal.create") ? `<button class="btn btn-primary btn-sm" id="new-je">${icon("plus")} ${ESC(t("journal.new"))}</button>` : "")}
        <div class="card">
          <div class="table-toolbar">
            <div class="chip-row" id="status-chips">
              ${STATUS_FILTERS.map(s => `<span class="chip ${state.status === s ? "active" : ""}" data-status="${s}">${ESC(s === "all" ? t("common.all") : t("status." + s))}</span>`).join("")}
            </div>
            <div style="flex:1"></div>
            <input id="je-q" placeholder="${ESC(t("common.search"))}" />
            <input id="je-from" type="date" value="" />
            <input id="je-to" type="date" value="" />
          </div>
          <div id="je-table">${renderTable([{ key: "entry_no", label: L("الرمز التعريفي", "Reference ID") }], [], {})}</div>
        </div>`;
      const load = async () => {
        const qs = new URLSearchParams({ status: state.status, q: state.q, from: state.from, to: state.to, per_page: 60 });
        const data = await api("/api/journal-entries?" + qs.toString());
        $("#je-table").innerHTML = renderTable([
          { key: "entry_no", label: L("الرمز التعريفي", "Reference ID"), render: r => `<a href="#/journal/${r.id}"><strong>${ESC(r.entry_no)}</strong></a>` },
          { key: "entry_date", label: t("common.date"), render: r => fmtDate(r.entry_date) },
          { key: "memo", label: t("common.description"), cls: "wrap", render: r => `<span title="${ESC(r.memo)}">${ESC(r.memo.slice(0, 52))}${r.memo.length > 52 ? "…" : ""}</span>` },
          { key: "entry_type", label: t("journal.type"), render: r => `<span class="pill">${ESC(r.entry_type)}</span>` },
          { key: "total_debit_cents", label: t("common.debit"), cls: "num", render: r => fmtMoney(r.total_debit_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "created_by_name", label: t("journal.createdBy"), render: r => ESC(r.created_by_name || "—") },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${!r.source_type && r.status === "draft" && can("journal.edit") ? editButton(`/api/journal-entries/${r.id}`,
                  `القيد ${r.entry_no}`, `entry ${r.entry_no}`, [
                    { name: "entry_date", label: t("common.date"), type: "date", value: r.entry_date },
                    { name: "memo", label: t("common.description"), required: true, value: r.memo },
                  ]) : ""}
              ${(can("journal.delete") && !r.source_type)
                ? deleteButton(`/api/journal-entries/${r.id}`, `القيد ${r.entry_no}`, `entry ${r.entry_no}`)
                : "" }` },
        ], data.entries, {
          onRowClick: null,
          empty: t("common.empty")
        });
        $$("#je-table tbody tr").forEach(tr => tr.onclick = (e) => {
          if (e.target.closest("a")) return;
          const idx = Number(tr.dataset.idx);
          location.hash = "#/journal/" + data.entries[idx].id;
        });
        $("#je-table").insertAdjacentHTML("beforeend",
          `<div class="muted small" style="padding:10px 14px">${data.total} ${ESC(t("common.rows"))}</div>`);
      };
      await load();
      $$("#status-chips .chip").forEach(chip => chip.onclick = () => {
        state.status = chip.dataset.status;
        $$("#status-chips .chip").forEach(c => c.classList.toggle("active", c === chip));
        load();
      });
      $("#je-q").oninput = (e) => { state.q = e.target.value; clearTimeout(window.__jt); window.__jt = setTimeout(load, 250); };
      $("#je-from").onchange = (e) => { state.from = e.target.value; load(); };
      $("#je-to").onchange = (e) => { state.to = e.target.value; load(); };
      if (can("journal.create")) $("#new-je").onclick = () => PAGES.journal.newEntry();
    },

    async detail(view, id) {
      const data = await api("/api/journal-entries/" + id);
      const e = data.entry;
      setTitle("journal.details", [e.entry_no]);
      const total = data.lines.reduce((s, l) => s + l.debit_cents, 0);
      const actions = [];
      if (e.status === "draft" && can("journal.create")) actions.push(`<button class="btn btn-primary btn-sm" id="act-submit">${icon("upload")} ${ESC(t("common.submit"))}</button>`);
      if (e.status === "review" && can("journal.review")) {
        actions.push(`<button class="btn btn-ok btn-sm" id="act-review">${icon("check")} ${ESC(t("common.review"))}</button>`);
        actions.push(`<button class="btn btn-danger btn-sm" id="act-reject">${icon("close")} ${ESC(t("common.reject"))}</button>`);
      }
      if (e.status === "approved" && can("journal.post")) actions.push(`<button class="btn btn-primary btn-sm" id="act-post">${icon("download")} ${ESC(t("common.post"))}</button>`);
      if (e.status === "posted" && can("journal.reverse")) actions.push(`<button class="btn btn-danger btn-sm" id="act-reverse">${icon("undo")} ${ESC(t("common.reverse"))}</button>`);
      if (e.status === "draft" && can("journal.create") && !e.source_type) {
        actions.push(editButton(`/api/journal-entries/${id}`, `القيد ${e.entry_no}`, `entry ${e.entry_no}`, [
          { name: "entry_date", label: t("common.date"), type: "date", value: e.entry_date },
          { name: "memo", label: t("common.description"), required: true, value: e.memo },
        ]));
      }
      if (can("journal.delete") && !e.source_type) actions.push(deleteButton(`/api/journal-entries/${id}`, `القيد ${e.entry_no}`, `entry ${e.entry_no}`, { delBack: "#/journal" }));
      actions.push(printBtn());

      view.innerHTML = `${pageHead("journal.details", "journal.subtitle", actions.join(""))}
        <div class="grid cols-4" style="margin-bottom:16px">
          ${kpi({ label: t("common.amount"), value: total, icon: icon("assets") })}
          ${kpi({ label: t("common.status"), value: statusBadge(e.status), icon: icon("pie"), raw: true })}
          ${kpi({ label: t("common.date"), value: fmtDate(e.entry_date), icon: icon("list"), raw: true })}
          ${kpi({ label: t("journal.type"), value: ESC(e.entry_type), icon: icon("settings"), raw: true })}
        </div>
        ${card(t("journal.lines"), renderTable([
            { key: "line_no", label: L("الرمز التعريفي", "Reference ID"), cls: "num" },
            { key: "account_code", label: t("common.code"), render: r => `<span class="mono">${ESC(r.account_code)}</span>` },
            { key: "account_ar", label: t("common.account"), render: r => ESC(APP.lang === "ar" ? r.account_ar : r.account_en) },
            { key: "description", label: t("common.description") },
            { key: "debit_cents", label: t("common.debit"), cls: "num", render: r => r.debit_cents ? fmtMoney(r.debit_cents) : "" },
            { key: "credit_cents", label: t("common.credit"), cls: "num", render: r => r.credit_cents ? fmtMoney(r.credit_cents) : "" },
          ], data.lines, {
            footer: `<tr><td colspan="4">${ESC(t("common.total"))}</td>
              <td class="num">${fmtMoney(data.lines.reduce((s, l) => s + l.debit_cents, 0))}</td>
              <td class="num">${fmtMoney(data.lines.reduce((s, l) => s + l.credit_cents, 0))}</td></tr>`
        }))}
        <div class="split" style="margin-top:16px">
            ${card(t("common.details"), `
              <div class="stat-line"><span>${ESC(t("journal.memo"))}</span><strong style="text-align:end">${ESC(e.memo)}</strong></div>
              <div class="stat-line"><span>${ESC(t("journal.createdBy"))}</span><strong>${e.created_by || "—"}</strong></div>
              <div class="stat-line"><span>${ESC(t("journal.approvedBy"))}</span><strong>${e.approved_by || "—"}</strong></div>
              <div class="stat-line"><span>${ESC(t("journal.postedAt"))}</span><strong>${ESC(e.posted_at || "—")}</strong></div>
              ${e.reversal_of_id ? `<div class="stat-line"><span>${ESC(t("journal.reversalOf"))}</span>
                <strong><a href="#/journal/${e.reversal_of_id}">#${e.reversal_of_id}</a></strong></div>` : ""}
              ${e.reversed_by_id ? `<div class="stat-line"><span>${ESC(t("common.reverse"))}</span>
                <strong><a href="#/journal/${e.reversed_by_id}">#${e.reversed_by_id}</a></strong></div>` : ""}
            `)}
            ${card(t("journal.workflow"), `
              <div class="stat-line"><span>1. ${ESC(t("status.draft"))}</span><strong>${icon("check", "ico-sm")}</strong></div>
              <div class="stat-line"><span>2. ${ESC(t("status.review"))}</span><strong>${["review", "approved", "posted", "reversed"].includes(e.status) ? icon("check", "ico-sm") : "—"}</strong></div>
              <div class="stat-line"><span>3. ${ESC(t("status.approved"))}</span><strong>${["approved", "posted", "reversed"].includes(e.status) ? icon("check", "ico-sm") : "—"}</strong></div>
              <div class="stat-line"><span>4. ${ESC(t("status.posted"))}</span><strong>${["posted", "reversed"].includes(e.status) ? icon("check", "ico-sm") : "—"}</strong></div>
              <p class="field-hint" style="margin-top:10px">${ESC(L("القيود التي تتجاوز 200,000 ج.م تحتاج اعتماد المدير المالي، ولا يمكن للمنشئ اعتماد قيده.",
                "Entries above EGP 200,000 require Financial Manager approval; creators cannot approve their own entries."))}</p>
            `)}
        </div>`;

      const act = (btn, fn) => { const b = document.getElementById(btn); if (b) b.onclick = fn; };
      act("act-submit", async () => {
        try { await api(`/api/journal-entries/${id}/submit`, { method: "POST", body: {} }); toast(t("toast.submitted")); render(); }
        catch (e) { toast(e.message, "err"); }
      });
      act("act-review", async () => {
        try { const res = await api(`/api/journal-entries/${id}/review`, { method: "POST", body: { decision: "approve" } });
          toast(res.note === "REQUIRES_APPROVAL" ? L("تمت المراجعة — بانتظار اعتماد المدير المالي", "Reviewed — awaiting FM approval") : t("toast.approved")); render(); }
        catch (e) { toast(e.message, "err"); }
      });
      act("act-reject", () => openForm({
        title: t("common.reject"), fields: [{ name: "comment", label: t("journal.comment"), type: "textarea", required: true }],
        onSubmit: async (d) => { await api(`/api/journal-entries/${id}/review`, { method: "POST", body: { decision: "reject", comment: d.comment } }); toast(t("toast.rejected")); render(); }
      }));
      act("act-post", async () => {
        try { await api(`/api/journal-entries/${id}/post`, { method: "POST", body: {} }); toast(t("toast.posted")); render(); }
        catch (e) { toast(e.message, "err"); }
      });
      act("act-reverse", () => openForm({
        title: t("common.reverse"),
        fields: [
          { name: "reason", label: t("journal.reverseReason"), type: "textarea", required: true },
          { name: "date", label: t("common.date"), type: "date", value: today(), required: true },
        ],
        onSubmit: async (d) => { await api(`/api/journal-entries/${id}/reverse`, { method: "POST", body: d }); toast(t("toast.saved")); render(); }
      }));
    },

    async newEntry() {
      const accs = await api("/api/accounts");
      const postable = accs.accounts.filter(a => a.is_postable && a.is_active);
      const accOptions = postable.map(a => ({ value: a.id, label: `${a.code} — ${APP.lang === "ar" ? a.name_ar : a.name_en}` }));
      const rowHtml = () => `
        <tr>
          <td><select class="input line-acc">${accOptions.map(o => `<option value="${o.value}">${ESC(o.label)}</option>`).join("")}</select></td>
          <td><input class="input line-desc" placeholder="${ESC(t("common.description"))}"/></td>
          <td><input class="input line-debit" type="number" step="0.01" min="0" placeholder="0.00"/></td>
          <td><input class="input line-credit" type="number" step="0.01" min="0" placeholder="0.00"/></td>
          <td><button class="icon-btn rm-line" title="حذف">${icon("close")}</button></td>
        </tr>`;
      openModal({
        title: t("journal.new"), wide: true,
        body: `
          <div class="grid cols-2" style="gap:0 16px">
            <div class="field"><span>${ESC(t("common.date"))} *</span><input type="date" id="je-date" value="${today()}"/></div>
            <div class="field"><span>${ESC(t("journal.memo"))} *</span><input id="je-memo" placeholder="${ESC(L("مثال: قيد تسوية مصروفات مستحقة", "e.g. Accrued expenses adjustment"))}"/></div>
          </div>
          <div class="table-wrap" style="border:1px solid var(--border);border-radius:12px">
            <table class="data compact" id="je-lines">
              <thead><tr><th>${ESC(t("common.account"))}</th><th>${ESC(t("common.description"))}</th>
                <th style="width:130px">${ESC(t("common.debit"))}</th><th style="width:130px">${ESC(t("common.credit"))}</th><th></th></tr></thead>
              <tbody>${rowHtml()}${rowHtml()}</tbody>
            </table>
          </div>
          <div style="display:flex;gap:16px;align-items:center;margin-top:12px;flex-wrap:wrap">
            <button class="btn btn-sm" id="add-line">${icon("plus")} ${ESC(t("journal.addLine"))}</button>
            <span class="muted small">${ESC(t("common.debit"))}: <strong id="sum-d">0.00</strong></span>
            <span class="muted small">${ESC(t("common.credit"))}: <strong id="sum-c">0.00</strong></span>
            <span id="balance-flag" class="badge gray">—</span>
            <div style="flex:1"></div>
            <label class="checkbox"><input type="checkbox" id="submit-now" checked/> ${ESC(L("إرسال للمراجعة بعد الحفظ", "Submit for review after saving"))}</label>
          </div>
          <div class="form-error hidden" id="je-error"></div>`,
        footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
                 <button class="btn btn-primary" id="save-je">${ESC(t("common.save"))}</button>`,
        onMount: (root, close) => {
          const linesBody = $("#je-lines tbody", root);
          const recalc = () => {
            let d = 0, c = 0;
            $$("#je-lines tbody tr", root).forEach(tr => {
              d += Number($(".line-debit", tr).value || 0);
              c += Number($(".line-credit", tr).value || 0);
            });
            $("#sum-d", root).textContent = d.toFixed(2);
            $("#sum-c", root).textContent = c.toFixed(2);
            const flag = $("#balance-flag", root);
            const ok = Math.abs(d - c) < 0.005 && d > 0;
            flag.className = "badge " + (ok ? "green" : "amber");
            flag.textContent = ok ? t("journal.balanced") : `${t("journal.difference")}: ${(d - c).toFixed(2)}`;
          };
          const wire = () => {
            $$(".rm-line", root).forEach(b => b.onclick = () => {
              if ($$("#je-lines tbody tr", root).length > 2) { b.closest("tr").remove(); recalc(); }
            });
            $$(".line-debit,.line-credit", root).forEach(inp => inp.oninput = () => {
              if (inp.classList.contains("line-debit") && Number(inp.value) > 0) $(".line-credit", inp.closest("tr")).value = "";
              if (inp.classList.contains("line-credit") && Number(inp.value) > 0) $(".line-debit", inp.closest("tr")).value = "";
              recalc();
            });
            recalc();
          };
          $("#add-line", root).onclick = () => { linesBody.insertAdjacentHTML("beforeend", rowHtml()); wire(); };
          wire();
          $("#save-je", root).onclick = async () => {
            const err = $("#je-error", root);
            err.classList.add("hidden");
            const lines = $$("#je-lines tbody tr", root).map(tr => ({
              account_id: Number($(".line-acc", tr).value),
              description: $(".line-desc", tr).value,
              debit: Number($(".line-debit", tr).value || 0),
              credit: Number($(".line-credit", tr).value || 0),
            })).filter(l => l.debit || l.credit);
            const memo = $("#je-memo", root).value.trim();
            if (!memo) { showErr(root, t("journal.memo") + ": " + t("common.required")); return; }
            const d = lines.reduce((s, l) => s + l.debit, 0);
            const c = lines.reduce((s, l) => s + l.credit, 0);
            if (Math.abs(d - c) > 0.005) { showErr(root, t("common.unbalanced")); return; }
            try {
              const res = await api("/api/journal-entries", { method: "POST", body: { entry_date: $("#je-date", root).value, memo, lines } });
              if ($("#submit-now", root).checked) {
                try { await api(`/api/journal-entries/${res.id}/submit`, { method: "POST", body: {} }); } catch (e) { /* requires another user */ }
              }
              toast(t("common.saved")); close(); location.hash = "#/journal/" + res.id;
            } catch (e) { showErr(root, e.message); }
          };
        }
      });
    }
  };

  /* ==================================================================
     SALES
     ================================================================== */
  PAGES.sales = {
    perm: "sales.view",
    async render(view, params) {
      if (params && params[0]) return salesDetail(view, params[0]);
      setTitle("nav.sales");
      const data = await api("/api/sales-invoices");
      view.innerHTML = `${pageHead("sales.title", "sales.subtitle",
        `${can("sales.create") ? `<button class="btn btn-primary btn-sm" id="new-si">${icon("plus")} ${ESC(t("sales.new"))}</button>` : ""}`)}
        ${card("", renderTable([
          { key: "invoice_no", label: L("الرمز التعريفي", "Reference ID"), render: r => `<a href="#/sales/${r.id}"><strong>${ESC(r.invoice_no)}</strong></a>` },
          { key: "invoice_date", label: t("common.date"), render: r => fmtDate(r.invoice_date) },
          { key: "customer_ar", label: t("common.customer"), render: r => ESC(APP.lang === "ar" ? r.customer_ar : r.customer_en) },
          { key: "due_date", label: t("common.dueDate"), render: r => fmtDate(r.due_date) },
          { key: "total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.total_cents) },
          { key: "paid_cents", label: t("common.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
          { key: "remaining", label: t("common.remaining"), cls: "num", render: r => fmtMoney(r.total_cents - r.paid_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${r.status === "draft" && can("sales.edit") ? editButton(`/api/sales-invoices/${r.id}`,
                  `فاتورة المبيعات ${r.invoice_no}`, `sales invoice ${r.invoice_no}`, [
                    { name: "customer_id", label: t("nav.customers"), type: "select", value: r.customer_id,
                      options: [{ value: r.customer_id, label: `${r.customer_ar} (${r.customer_code})` }] },
                    { name: "invoice_date", label: t("common.date"), type: "date", value: r.invoice_date },
                    { name: "due_date", label: t("common.dueDate"), type: "date", value: r.due_date || "" },
                    { name: "notes", label: t("common.description"), value: r.notes || "" },
                  ]) : ""}
              ${can("sales.delete")
                ? deleteButton(`/api/sales-invoices/${r.id}`, `فاتورة المبيعات ${r.invoice_no}`, `sales invoice ${r.invoice_no}`)
                : "" }` },
        ], data.invoices, {
          footer: `<tr><td colspan="4">${ESC(t("common.total"))}</td>
            <td class="num">${fmtMoney(data.invoices.reduce((s, r) => s + r.total_cents, 0))}</td>
            <td class="num">${fmtMoney(data.invoices.reduce((s, r) => s + r.paid_cents, 0))}</td>
            <td class="num">${fmtMoney(data.invoices.reduce((s, r) => s + r.total_cents - r.paid_cents, 0))}</td><td></td><td></td></tr>`
        }))}`;
      if (can("sales.create")) $("#new-si").onclick = () => PAGES.sales.newInvoice();
    },

    async newInvoice() {
      const [cust, prods, meta] = await Promise.all([api("/api/customers"), api("/api/products"), api("/api/meta")]);
      const customers = cust.customers.filter(c => c.is_active);
      const products = prods.products.filter(p => p.is_active);
      const rowHtml = () => `<tr>
          <td><select class="input li-prod">${products.map(p => `<option value="${p.id}" data-price="${p.sale_price_cents}" data-tax="${p.tax_rate_bp}">${ESC(p.sku)} — ${ESC(p.name_ar)}</option>`).join("")}</select></td>
          <td><input class="input li-qty" type="number" step="0.001" min="0" value="1"/></td>
          <td><input class="input li-price" type="number" step="0.01" min="0"/></td>
          <td><input class="input li-tax" type="number" step="0.01" min="0" value="14"/></td>
          <td class="num li-total mono">0.00</td>
          <td><button class="icon-btn rm">${icon("close")}</button></td></tr>`;
      openModal({
        title: t("sales.new"), wide: true,
        body: `
          <div class="grid cols-3" style="gap:0 16px">
            <div class="field"><span>${ESC(t("common.customer"))} *</span>
              <select id="si-cust">${customers.map(c => `<option value="${c.id}">${ESC(c.code)} — ${ESC(APP.lang === "ar" ? c.name_ar : c.name_en)}</option>`).join("")}</select></div>
            <div class="field"><span>${ESC(t("sales.invoiceDate"))}</span><input type="date" id="si-date" value="${today()}"/></div>
            <div class="field"><span>${ESC(t("common.dueDate"))}</span><input type="date" id="si-due" value="${today()}"/></div>
            <div class="field"><span>${ESC(t("common.warehouse"))}</span>
              <select id="si-wh">${meta.warehouses.map(w => `<option value="${w.id}">${ESC(w.name_ar)}</option>`).join("")}</select></div>
            <div class="field" style="grid-column:span 2"><span>${ESC(t("common.notes"))}</span><input id="si-notes"/></div>
          </div>
          <div class="table-wrap" style="border:1px solid var(--border);border-radius:12px">
            <table class="data compact" id="si-lines">
              <thead><tr><th>${ESC(L("الصنف", "Product"))}</th><th>${ESC(t("common.qty"))}</th><th>${ESC(t("common.price"))}</th>
                <th>${ESC(t("common.tax"))} %</th><th class="num">${ESC(t("common.total"))}</th><th></th></tr></thead>
              <tbody>${rowHtml()}</tbody>
            </table>
          </div>
          <div style="display:flex;gap:18px;align-items:center;margin-top:12px;flex-wrap:wrap">
            <button class="btn btn-sm" id="si-add">${icon("plus")} ${ESC(t("common.add"))}</button>
            <span class="muted small">${ESC(t("common.subtotal"))}: <strong id="si-sub">0.00</strong></span>
            <span class="muted small">${ESC(t("common.tax"))}: <strong id="si-tax">0.00</strong></span>
            <span class="muted small">${ESC(t("common.total"))}: <strong id="si-total">0.00</strong></span>
            <div style="flex:1"></div>
            <label class="checkbox"><input type="checkbox" id="si-post" checked/> ${ESC(L("ترحيل الفاتورة فورًا", "Post invoice immediately"))}</label>
          </div>
          <div class="form-error hidden" id="si-error"></div>`,
        footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
                 <button class="btn btn-primary" id="si-save">${ESC(t("common.save"))}</button>`,
        onMount: (root, close) => {
          const recalc = () => {
            let sub = 0, tax = 0;
            $$("#si-lines tbody tr", root).forEach(tr => {
              const q = Number($(".li-qty", tr).value || 0), p = Number($(".li-price", tr).value || 0);
              const tRate = Number($(".li-tax", tr).value || 0);
              const line = q * p, lineTax = line * tRate / 100;
              sub += line; tax += lineTax;
              $(".li-total", tr).textContent = (line + lineTax).toFixed(2);
            });
            $("#si-sub", root).textContent = sub.toFixed(2);
            $("#si-tax", root).textContent = tax.toFixed(2);
            $("#si-total", root).textContent = (sub + tax).toFixed(2);
          };
          const wire = () => {
            $$(".li-prod", root).forEach(sel => sel.onchange = () => {
              const tr = sel.closest("tr");
              $(".li-price", tr).value = (Number(sel.selectedOptions[0].dataset.price) / 100).toFixed(2);
              $(".li-tax", tr).value = (Number(sel.selectedOptions[0].dataset.tax) / 100).toFixed(2);
              recalc();
            });
            $$("input", root).forEach(i => i.oninput = recalc);
            $$(".rm", root).forEach(b => b.onclick = () => {
              if ($$("#si-lines tbody tr", root).length > 1) { b.closest("tr").remove(); recalc(); }
            });
            $$(".li-prod", root).forEach(sel => sel.dispatchEvent(new Event("change")));
          };
          wire();
          $("#si-add", root).onclick = () => {
            $("#si-lines tbody", root).insertAdjacentHTML("beforeend", rowHtml());
            wire();
          };
          $("#si-save", root).onclick = async () => {
            try {
              const lines = $$("#si-lines tbody tr", root).map(tr => ({
                product_id: Number($(".li-prod", tr).value),
                qty: Number($(".li-qty", tr).value || 0),
                unit_price: Number($(".li-price", tr).value || 0),
                tax_rate_bp: Math.round(Number($(".li-tax", tr).value || 0) * 100),
              }));
              const res = await api("/api/sales-invoices", {
                method: "POST", body: {
                  customer_id: Number($("#si-cust", root).value),
                  invoice_date: $("#si-date", root).value,
                  due_date: $("#si-due", root).value,
                  warehouse_id: Number($("#si-wh", root).value),
                  notes: $("#si-notes", root).value,
                  lines, post: $("#si-post", root).checked,
                }
              });
              toast(t("common.saved")); close(); location.hash = "#/sales/" + res.id;
            } catch (e) { showErr(root, e.message); }
          };
        }
      });
    }
  };

  async function salesDetail(view, id) {
    const d = await api("/api/sales-invoices/" + id);
    const inv = d.invoice;
    setTitle("sales.title", [inv.invoice_no]);
    const actions = [];
    if (inv.status === "draft" && can("sales.post")) actions.push(`<button class="btn btn-primary btn-sm" id="si-post-now">${icon("download")} ${ESC(t("common.post"))}</button>`);
    if (["posted", "partially_paid"].includes(inv.status) && can("payments.create")) actions.push(`<button class="btn btn-ok btn-sm" id="si-receipt">${icon("coins")} ${ESC(t("payments.receipt"))}</button>`);
    if (inv.status === "draft" && can("sales.edit")) actions.push(editButton(`/api/sales-invoices/${id}`,
      `فاتورة المبيعات ${inv.invoice_no}`, `sales invoice ${inv.invoice_no}`, [
        { name: "invoice_date", label: t("common.date"), type: "date", value: inv.invoice_date },
        { name: "due_date", label: t("common.dueDate"), type: "date", value: inv.due_date || "" },
        { name: "warehouse_id", label: t("common.warehouse"), type: "number", value: inv.warehouse_id || "" },
        { name: "notes", label: t("common.description"), value: inv.notes || "" },
      ]));
    if (can("sales.delete")) actions.push(deleteButton(`/api/sales-invoices/${id}`, `فاتورة المبيعات ${inv.invoice_no}`, `sales invoice ${inv.invoice_no}`, { delBack: "#/sales" }));
    actions.push(docToolbar("sales", id));
    view.innerHTML = `${pageHead(inv.invoice_no, "sales.subtitle", actions.join(""))}
      <div class="grid cols-4" style="margin-bottom:16px">
        ${kpi({ label: t("common.total"), value: inv.total_cents, icon: icon("assets") })}
        ${kpi({ label: t("common.paid"), value: inv.paid_cents, icon: icon("check"), tone: "ok" })}
        ${kpi({ label: t("common.remaining"), value: inv.total_cents - inv.paid_cents, icon: icon("pie"), tone: "warn" })}
        ${kpi({ label: t("common.status"), value: statusBadge(inv.status), icon: icon("list"), raw: true })}
      </div>
      ${card(L("سطور الفاتورة", "Invoice lines"), renderTable([
          { key: "line_no", label: L("الرمز التعريفي", "Reference ID"), cls: "num" },
          { key: "product_ar", label: L("الصنف", "Product"), cls: "wrap" },
          { key: "qty_milli", label: t("common.qty"), cls: "num", render: r => fmtNum(r.qty_milli / 1000, 0) },
          { key: "unit_price_cents", label: t("common.price"), cls: "num", render: r => fmtMoney(r.unit_price_cents) },
          { key: "line_subtotal_cents", label: t("common.subtotal"), cls: "num", render: r => fmtMoney(r.line_subtotal_cents) },
          { key: "line_tax_cents", label: t("common.tax"), cls: "num", render: r => fmtMoney(r.line_tax_cents) },
          { key: "line_total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.line_total_cents) },
        ], d.lines, {
          footer: `<tr><td colspan="4">${ESC(t("common.total"))}</td>
            <td class="num" data-label="${ESC(t("common.subtotal"))}">${fmtMoney(inv.subtotal_cents)}</td>
            <td class="num" data-label="${ESC(t("common.tax"))}">${fmtMoney(inv.tax_cents)}</td>
            <td class="num" data-label="${ESC(t("common.total"))}">${fmtMoney(inv.total_cents)}</td></tr>`
      }))}
      <div class="split" style="margin-top:16px">
          ${card(t("common.details"), `
            <div class="stat-line"><span>${ESC(t("common.customer"))}</span><strong>${ESC(APP.lang === "ar" ? inv.customer_ar : inv.customer_en)}</strong></div>
            <div class="stat-line"><span>${ESC(t("sales.invoiceDate"))}</span><strong>${fmtDate(inv.invoice_date)}</strong></div>
            <div class="stat-line"><span>${ESC(t("common.dueDate"))}</span><strong>${fmtDate(inv.due_date)}</strong></div>
            <div class="stat-line"><span>${ESC(t("journal.createdBy"))}</span><strong>${ESC(inv.created_by_name || "—")}</strong></div>
            <div class="stat-line"><span>${ESC(t("journal.postedAt"))}</span><strong>${ESC(inv.posted_at || "—")}</strong></div>
            ${inv.notes ? `<div class="stat-line"><span>${ESC(t("common.notes"))}</span><strong>${ESC(inv.notes)}</strong></div>` : ""}`)}
          ${card(t("payments.allocate"), d.allocations.length ? d.allocations.map(a =>
            `<div class="stat-line"><span>${ESC(a.payment_no)} <small class="muted">${fmtDate(a.payment_date)}</small></span>
              <strong>${fmtMoney(a.amount_cents)}</strong></div>`).join("") : `<div class="muted small">${ESC(t("common.empty"))}</div>`)}
      </div>`;
    const btn = $("#si-post-now");
    if (btn) btn.onclick = async () => {
      try { await api(`/api/sales-invoices/${id}/post`, { method: "POST", body: {} }); toast(t("toast.posted")); render(); }
      catch (e) { toast(e.message, "err"); }
    };
    const rb = $("#si-receipt");
    if (rb) rb.onclick = () => PAGES.payments.newVoucher("receipt", { party_type: "customer", party_id: inv.customer_id, invoice_id: id, amount: (inv.total_cents - inv.paid_cents) / 100 });
    wireExports(view);
  }

  /* ==================================================================
     PURCHASES
     ================================================================== */
  PAGES.purchases = {
    perm: "purchases.view",
    async render(view, params) {
      if (params && params[0]) return purchaseDetail(view, params[0]);
      setTitle("nav.purchases");
      const data = await api("/api/purchase-invoices");
      view.innerHTML = `${pageHead("purchases.title", "purchases.subtitle",
        can("purchases.create") ? `<button class="btn btn-primary btn-sm" id="new-pi">${icon("plus")} ${ESC(t("purchases.new"))}</button>` : "")}
        ${card("", renderTable([
          { key: "invoice_no", label: L("الرمز التعريفي", "Reference ID"), render: r => `<a href="#/purchases/${r.id}"><strong>${ESC(r.invoice_no)}</strong></a>` },
          { key: "invoice_date", label: t("common.date"), render: r => fmtDate(r.invoice_date) },
          { key: "vendor_ar", label: t("common.vendor"), render: r => ESC(APP.lang === "ar" ? r.vendor_ar : r.vendor_en) },
          { key: "supplier_ref", label: t("purchases.supplierRef") },
          { key: "due_date", label: t("common.dueDate"), render: r => fmtDate(r.due_date) },
          { key: "total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.total_cents) },
          { key: "paid_cents", label: t("common.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "three_way_match", label: t("purchases.threeWay"), render: r => r.three_way_match ? `<span class="badge green">${icon("check", "ico-sm")}</span>` : `<span class="badge gray">—</span>` },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${r.status === "draft" && can("purchases.edit") ? editButton(`/api/purchase-invoices/${r.id}`,
                  `فاتورة المشتريات ${r.invoice_no}`, `purchase invoice ${r.invoice_no}`, [
                    { name: "invoice_date", label: t("common.date"), type: "date", value: r.invoice_date },
                    { name: "due_date", label: t("common.dueDate"), type: "date", value: r.due_date || "" },
                    { name: "supplier_ref", label: L("مرجع المورد", "Supplier ref"), value: r.supplier_ref || "" },
                    { name: "po_number", label: t("purchases.po"), value: r.po_number || "" },
                    { name: "grn_number", label: t("purchases.grn"), value: r.grn_number || "" },
                    { name: "notes", label: t("common.description"), value: r.notes || "" },
                  ]) : ""}
              ${can("purchases.delete")
                ? deleteButton(`/api/purchase-invoices/${r.id}`, `فاتورة المشتريات ${r.invoice_no}`, `purchase invoice ${r.invoice_no}`)
                : "" }` },
        ], data.invoices, {
          footer: `<tr><td colspan="5">${ESC(t("common.total"))}</td>
            <td class="num">${fmtMoney(data.invoices.reduce((s, r) => s + r.total_cents, 0))}</td>
            <td class="num">${fmtMoney(data.invoices.reduce((s, r) => s + r.paid_cents, 0))}</td><td colspan="2"></td><td></td></tr>`
        }))}`;
      if (can("purchases.create")) $("#new-pi").onclick = () => PAGES.purchases.newInvoice();
    },

    async newInvoice() {
      const [vend, prods, meta] = await Promise.all([api("/api/vendors"), api("/api/products"), api("/api/meta")]);
      const vendors = vend.vendors.filter(v => v.is_active);
      // every active item (goods *and* services) plus a free-text line, because a
      // shipping company buys services — نولون، تفريغ، صيانة، إيجار لودر — that are
      // not in the item master at all. Previously only stocked goods were offered,
      // so with a service-only catalogue the form had no selectable line.
      const products = prods.products.filter(p => p.is_active);
      if (!vendors.length) {
        openModal({
          title: t("purchases.new"), narrow: true,
          body: `<p class="muted small" style="margin:0">${ESC(L(
            "لا يوجد موردون مسجّلون بعد. أضف موردًا أولًا من شاشة الموردين ثم أعد المحاولة.",
            "No vendors exist yet. Add a vendor from the vendors screen first."))}</p>`,
          footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
                   <a class="btn btn-primary" href="#/vendors">${ESC(L("شاشة الموردين", "Vendors"))}</a>`,
        });
        return;
      }
      const FREE = "__free__";   // pseudo-product: free-text service line
      const optionsHtml = products.map(p =>
        `<option value="${p.id}" data-price="${p.purchase_price_cents}" data-tax="${p.tax_rate_bp}">${ESC(p.sku)} — ${ESC(p.name_ar)}</option>`).join("")
        + `<option value="${FREE}" data-price="0" data-tax="0">${ESC(L("＋ بند حر (خدمة / لا يوجد صنف)", "＋ Free line (service / no item)"))}</option>`;
      const rowHtml = () => `<tr>
          <td><select class="input li-prod">${optionsHtml}</select>
              <input class="input li-desc hidden" placeholder="${ESC(L("وصف البند", "Line description"))}" style="margin-top:6px"/></td>
          <td><input class="input li-qty" type="number" step="0.001" min="0" value="1"/></td>
          <td><input class="input li-price" type="number" step="0.01" min="0"/></td>
          <td><input class="input li-tax" type="number" step="0.01" min="0" value="14"/></td>
          <td class="num li-total mono">0.00</td>
          <td><button class="icon-btn rm">${icon("close")}</button></td></tr>`;
      openModal({
        title: t("purchases.new"), wide: true,
        body: `
          <div class="grid cols-3" style="gap:0 16px">
            <div class="field"><span>${ESC(t("common.vendor"))} *</span>
              <select id="pi-vend">${vendors.map(v => `<option value="${v.id}">${ESC(v.code)} — ${ESC(APP.lang === "ar" ? v.name_ar : v.name_en)}</option>`).join("")}</select></div>
            <div class="field"><span>${ESC(t("purchases.supplierRef"))}</span><input id="pi-ref" placeholder="INV-…"/></div>
            <div class="field" id="pi-wh-field"><span>${ESC(t("common.warehouse"))}</span>
              <select id="pi-wh"><option value="">${ESC(L("بدون مخزن (خدمات)", "No warehouse (services)"))}</option>
                ${meta.warehouses.map(w => `<option value="${w.id}">${ESC(w.name_ar)}</option>`).join("")}</select></div>
            <div class="field"><span>${ESC(t("sales.invoiceDate"))}</span><input type="date" id="pi-date" value="${today()}"/></div>
            <div class="field"><span>${ESC(t("common.dueDate"))}</span><input type="date" id="pi-due" value="${today()}"/></div>
            <div class="field"><span>${ESC(t("purchases.po"))}</span><input id="pi-po"/></div>
            <div class="field"><span>${ESC(t("purchases.grn"))}</span><input id="pi-grn"/></div>
          </div>
          <div class="table-wrap" style="border:1px solid var(--border);border-radius:12px">
            <table class="data compact" id="pi-lines">
              <thead><tr><th>${ESC(L("الصنف / البند", "Item / line"))}</th><th>${ESC(t("common.qty"))}</th><th>${ESC(t("common.price"))}</th>
                <th>${ESC(t("common.tax"))} %</th><th class="num">${ESC(t("common.total"))}</th><th></th></tr></thead>
              <tbody>${rowHtml()}</tbody></table>
          </div>
          <div style="display:flex;gap:18px;align-items:center;margin-top:12px;flex-wrap:wrap">
            <button class="btn btn-sm" id="pi-add">${icon("plus")} ${ESC(L("إضافة بند حر", "Add a line"))}</button>
            <span class="muted small">${ESC(t("common.total"))}: <strong id="pi-total">0.00</strong></span>
            <div style="flex:1"></div>
            <label class="checkbox"><input type="checkbox" id="pi-post" checked/> ${ESC(L("ترحيل الفاتورة فورًا", "Post invoice immediately"))}</label>
          </div>
          <div class="form-error hidden" id="pi-error"></div>`,
        footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
                 <button class="btn btn-primary" id="pi-save">${ESC(t("common.save"))}</button>`,
        onMount: (root, close) => {
          const recalc = () => {
            let total = 0;
            $$("#pi-lines tbody tr", root).forEach(tr => {
              const q = Number($(".li-qty", tr).value || 0), p = Number($(".li-price", tr).value || 0);
              const rate = Number($(".li-tax", tr).value || 0);
              const line = q * p * (1 + rate / 100);
              total += line;
              $(".li-total", tr).textContent = line.toFixed(2);
            });
            $("#pi-total", root).textContent = total.toFixed(2);
          };
          const syncRow = (sel) => {
            const tr = sel.closest("tr");
            const free = sel.value === FREE;
            const desc = $(".li-desc", tr);
            desc.classList.toggle("hidden", !free);
            if (!free) {
              $(".li-price", tr).value = (Number(sel.selectedOptions[0].dataset.price) / 100).toFixed(2);
              $(".li-tax", tr).value = (Number(sel.selectedOptions[0].dataset.tax) / 100).toFixed(2);
            }
            recalc();
          };
          const wire = () => {
            $$(".li-prod", root).forEach(sel => { sel.onchange = () => syncRow(sel); });
            $$("input", root).forEach(i => i.oninput = recalc);
            $$(".rm", root).forEach(b => b.onclick = () => {
              if ($$("#pi-lines tbody tr", root).length > 1) { b.closest("tr").remove(); recalc(); }
            });
            $$(".li-prod", root).forEach(sel => {
              const free = sel.value === FREE;
              $(".li-desc", sel.closest("tr")).classList.toggle("hidden", !free);
            });
          };
          wire();
          $("#pi-add", root).onclick = () => {
            $("#pi-lines tbody", root).insertAdjacentHTML("beforeend", rowHtml());
            wire();
            const rows = $$("#pi-lines tbody tr", root);
            const last = rows[rows.length - 1];
            const sel = $(".li-prod", last);
            sel.value = FREE;                    // new lines start as a free service line
            syncRow(sel);
            $(".li-desc", last).focus();
          };
          $("#pi-save", root).onclick = async () => {
            try {
              const rows = $$("#pi-lines tbody tr", root);
              const lines = [];
              let needsWarehouse = false;
              for (const tr of rows) {
                const sel = $(".li-prod", tr);
                const free = sel.value === FREE;
                const qty = Number($(".li-qty", tr).value || 0);
                const price = Number($(".li-price", tr).value || 0);
                const desc = ($(".li-desc", tr).value || "").trim();
                if (!qty && !price && !desc) continue;           // untouched row
                if (free) {
                  if (!desc) { showErr(root, L("اكتب وصف البند الحر", "Describe the free line")); return; }
                  if (!price) { showErr(root, L("أدخل مبلغ البند الحر", "Enter the free line amount")); return; }
                } else {
                  const prod = products.find(p => String(p.id) === String(sel.value));
                  if (prod && !prod.is_service) needsWarehouse = true;
                }
                lines.push({
                  product_id: free ? null : Number(sel.value),
                  description: free ? desc : ($(".li-desc", tr).value || "").trim() || undefined,
                  qty, unit_price: price,
                  tax_rate_bp: Math.round(Number($(".li-tax", tr).value || 0) * 100),
                });
              }
              if (!lines.length) { showErr(root, L("أضف بندًا واحدًا على الأقل", "Add at least one line")); return; }
              const warehouseId = Number($("#pi-wh", root).value || 0);
              if (needsWarehouse && !warehouseId) {
                showErr(root, L("اختر المخزن للبنود المخزنية", "Choose a warehouse for stocked items"));
                return;
              }
              const res = await api("/api/purchase-invoices", {
                method: "POST", body: {
                  vendor_id: Number($("#pi-vend", root).value),
                  supplier_ref: $("#pi-ref", root).value,
                  invoice_date: $("#pi-date", root).value,
                  due_date: $("#pi-due", root).value,
                  po_number: $("#pi-po", root).value,
                  grn_number: $("#pi-grn", root).value,
                  warehouse_id: warehouseId || null,
                  lines, post: $("#pi-post", root).checked,
                }
              });
              toast(t("common.saved")); close(); location.hash = "#/purchases/" + res.id;
            } catch (e) { showErr(root, e.message); }
          };
        }
      });
    }
  };

  async function purchaseDetail(view, id) {
    const d = await api("/api/purchase-invoices/" + id);
    const inv = d.invoice;
    setTitle("purchases.title", [inv.invoice_no]);
    const actions = [];
    if (inv.status === "draft" && can("purchases.post")) actions.push(`<button class="btn btn-primary btn-sm" id="pi-post-now">${icon("download")} ${ESC(t("common.post"))}</button>`);
    if (inv.status === "draft" && can("purchases.edit")) actions.push(editButton(`/api/purchase-invoices/${id}`,
      `فاتورة المشتريات ${inv.invoice_no}`, `purchase invoice ${inv.invoice_no}`, [
        { name: "invoice_date", label: t("common.date"), type: "date", value: inv.invoice_date },
        { name: "due_date", label: t("common.dueDate"), type: "date", value: inv.due_date || "" },
        { name: "supplier_ref", label: L("مرجع المورد", "Supplier ref"), value: inv.supplier_ref || "" },
        { name: "po_number", label: t("purchases.po"), value: inv.po_number || "" },
        { name: "grn_number", label: t("purchases.grn"), value: inv.grn_number || "" },
        { name: "notes", label: t("common.description"), value: inv.notes || "" },
      ]));
    if (can("purchases.delete")) actions.push(deleteButton(`/api/purchase-invoices/${id}`, `فاتورة المشتريات ${inv.invoice_no}`, `purchase invoice ${inv.invoice_no}`, { delBack: "#/purchases" }));
    actions.push(docToolbar("purchase", id));
    view.innerHTML = `${pageHead(inv.invoice_no, "purchases.subtitle", actions.join(""))}
      <div class="grid cols-4" style="margin-bottom:16px">
        ${kpi({ label: t("common.total"), value: inv.total_cents, icon: icon("assets") })}
        ${kpi({ label: t("common.paid"), value: inv.paid_cents, icon: icon("check"), tone: "ok" })}
        ${kpi({ label: t("common.remaining"), value: inv.total_cents - inv.paid_cents, icon: icon("pie"), tone: "warn" })}
        ${kpi({ label: t("common.status"), value: statusBadge(inv.status), icon: icon("list"), raw: true })}
      </div>
      ${card(L("سطور الفاتورة", "Invoice lines"), renderTable([
          { key: "line_no", label: L("الرمز التعريفي", "Reference ID"), cls: "num" },
          { key: "product_ar", label: L("الصنف", "Product"), cls: "wrap" },
          { key: "qty_milli", label: t("common.qty"), cls: "num", render: r => fmtNum(r.qty_milli / 1000, 0) },
          { key: "unit_price_cents", label: t("common.price"), cls: "num", render: r => fmtMoney(r.unit_price_cents) },
          { key: "line_subtotal_cents", label: t("common.subtotal"), cls: "num", render: r => fmtMoney(r.line_subtotal_cents) },
          { key: "line_tax_cents", label: t("common.tax"), cls: "num", render: r => fmtMoney(r.line_tax_cents) },
          { key: "line_total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.line_total_cents) },
        ], d.lines, {
          footer: `<tr><td colspan="4">${ESC(t("common.total"))}</td>
            <td class="num">${fmtMoney(inv.subtotal_cents)}</td><td class="num">${fmtMoney(inv.tax_cents)}</td>
            <td class="num">${fmtMoney(inv.total_cents)}</td></tr>`
      }))}
      <div style="margin-top:16px">
        ${card(t("common.details"), `
          <div class="stat-line"><span>${ESC(t("common.vendor"))}</span><strong>${ESC(APP.lang === "ar" ? inv.vendor_ar : inv.vendor_en)}</strong></div>
          <div class="stat-line"><span>${ESC(t("purchases.supplierRef"))}</span><strong>${ESC(inv.supplier_ref || "—")}</strong></div>
          <div class="stat-line"><span>${ESC(t("purchases.po"))}</span><strong>${ESC(inv.po_number || "—")}</strong></div>
          <div class="stat-line"><span>${ESC(t("purchases.grn"))}</span><strong>${ESC(inv.grn_number || "—")}</strong></div>
          <div class="stat-line"><span>${ESC(t("purchases.threeWay"))}</span><strong>${inv.three_way_match ? icon("check", "ico-sm") : "—"}</strong></div>
          <div class="stat-line"><span>${ESC(t("common.dueDate"))}</span><strong>${fmtDate(inv.due_date)}</strong></div>
          <div class="stat-line"><span>${ESC(t("journal.createdBy"))}</span><strong>${ESC(inv.created_by_name || "—")}</strong></div>
          <div class="stat-line"><span>${ESC(t("journal.postedAt"))}</span><strong>${ESC(inv.posted_at || "—")}</strong></div>`)}
      </div>`;
    const btn = $("#pi-post-now");
    if (btn) btn.onclick = async () => {
      try { await api(`/api/purchase-invoices/${id}/post`, { method: "POST", body: {} }); toast(t("toast.posted")); render(); }
      catch (e) { toast(e.message, "err"); }
    };
    wireExports(view);
  }

  /* ==================================================================
     PAYMENTS
     ================================================================== */
  PAGES.payments = {
    perm: "payments.view",
    async render(view) {
      setTitle("nav.payments");
      const data = await api("/api/payments");
      view.innerHTML = `${pageHead("payments.title", "payments.subtitle",
        can("payments.create") ? `<button class="btn btn-primary btn-sm" id="new-pay">${icon("plus")} ${ESC(t("payments.new"))}</button>` : "")}
        ${card("", renderTable([
          { key: "payment_no", label: L("الرمز التعريفي", "Reference ID"), render: r => `<strong>${ESC(r.payment_no)}</strong>` },
          { key: "payment_date", label: t("common.date"), render: r => fmtDate(r.payment_date) },
          { key: "payment_type", label: t("payments.type"), render: r => `<span class="badge ${r.payment_type === "receipt" ? "green" : "amber"}">${ESC(r.payment_type === "receipt" ? t("payments.receipt") : t("payments.payment"))}</span>` },
          { key: "party_name", label: t("payments.party"), render: r => ESC(r.party_name || "—") },
          { key: "gl_name", label: t("common.account"), render: r => `${ESC(r.gl_name)}<br><small class="muted">${ESC(r.gl_code)}</small>` },
          { key: "amount_cents", label: t("common.amount"), cls: "num", render: r => fmtMoney(r.amount_cents) },
          { key: "unallocated", label: t("payments.unallocated"), cls: "num", render: r => r.amount_cents - r.allocated_cents ? `<span class="neg">${fmtMoney(r.amount_cents - r.allocated_cents)}</span>` : "—" },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${can("payments.edit") ? `<button class="icon-btn edit" data-pay-edit="${r.id}"
                  title="${ESC(L("تعديل السند", "Edit voucher"))}" aria-label="${ESC(L("تعديل", "Edit"))}">${icon("edit")}</button>` : ""}
              ${can("payments.delete")
                ? deleteButton(`/api/payments/${r.id}`,
                    `${r.payment_type === "receipt" ? "سند التحصيل" : "سند الدفع"} ${r.payment_no}`,
                    `${r.payment_type === "receipt" ? "receipt" : "payment"} ${r.payment_no}`)
                : "" }` },
        ], data.payments))}`;
      if (can("payments.create")) $("#new-pay").onclick = () => PAGES.payments.newVoucher("receipt");
    },

    /** Edit a receipt/payment — works for drafts and for already-posted vouchers
     *  (the server unposts, applies, and re-posts in one transaction). */
    async editForm(paymentId, onDone) {
      const ar = APP.lang === "ar";
      const [banks, list] = await Promise.all([api("/api/banks"), api("/api/payments")]);
      const pay = (list.payments || []).find(p => p.id === Number(paymentId));
      if (!pay) { toast(ar ? "السند غير موجود" : "Voucher not found", "err"); return; }
      const accounts = banks.accounts.map(b => ({
        value: b.gl_account_id,
        label: `${APP.lang === "ar" ? b.name_ar : (b.name_en || b.name_ar)} (${b.gl_code})`,
        kind: b.account_type,
      }));
      const cashFirst = accounts.find(a => a.kind === "cash");
      const subject = `${pay.payment_type === "receipt" ? (ar ? "سند التحصيل" : "receipt") : (ar ? "سند الدفع" : "payment")} ${pay.payment_no}`;
      const original = {
        payment_date: pay.payment_date,
        amount: (pay.amount_cents / 100).toFixed(2),
        method: pay.method || "bank",
        gl_account_id: String(pay.gl_account_id),
        reference: pay.reference || "",
        notes: pay.notes || "",
      };
      openForm({
        title: (ar ? "تعديل " : "Edit ") + subject,
        wide: true,
        submitLabel: ar ? "حفظ التعديل" : "Save changes",
        fields: [
          { name: "payment_date", label: t("common.date"), type: "date", value: original.payment_date },
          { name: "amount", label: t("common.amount"), type: "number", step: "0.01", required: true, value: original.amount,
            hint: pay.allocated_cents ? (ar ? `المخصّص للفواتير ${fmtMoney(pay.allocated_cents)} — لا يمكن النزول تحته`
                                              : `Allocated ${fmtMoney(pay.allocated_cents)} — cannot go below`) : "" },
          { name: "method", label: ar ? "طريقة الدفع" : "Payment method", type: "select", value: original.method,
            options: [{ value: "cash", label: ar ? "نقدًا" : "Cash" },
                      { value: "bank", label: ar ? "تحويل بنكي" : "Bank transfer" },
                      { value: "cheque", label: ar ? "شيك" : "Cheque" }] },
          { name: "gl_account_id", label: ar ? "الصندوق / البنك" : "Cash / bank account", type: "select", value: original.gl_account_id,
            options: accounts.map(a => ({ value: a.value, label: a.label })) },
          { name: "reference", label: ar ? "المرجع" : "Reference", value: original.reference },
          { name: "notes", label: t("common.description"), value: original.notes },
        ],
        onSubmit: async (data) => {
          const changed = Object.keys(data).filter(k => String(data[k]) !== String(original[k]));
          if (!changed.length) { toast(ar ? "لا يوجد تغيير لحفظه" : "Nothing changed", "err"); return; }
          const moneyChanged = changed.some(k => ["payment_date", "amount", "gl_account_id", "method"].includes(k));
          const ok = await confirmAsync(
            ar ? `هل أنت متأكد من تعديل ${subject}؟` + (pay.status === "posted" && moneyChanged
                  ? "\nالسند مرحّل: سيُعاد ترحيله بقيمه الجديدة ويُسجَّل ذلك في سجل التدقيق."
                  : "\nسيُسجَّل التعديل في سجل التدقيق باسمك ووقته.")
               : `Are you sure you want to edit ${subject}?`,
            { title: ar ? "تأكيد التعديل" : "Confirm edit" });
          if (!ok) return;
          await api(`/api/payments/${pay.id}`, { method: "PATCH", body: data });
          toast(ar ? "تم حفظ التعديل" : "Changes saved");
          if (onDone) await onDone(); else await render();
        },
      });
    },

    async newVoucher(type = "receipt", preset = {}) {
      const [banks, cust, vend, sinv, pinv] = await Promise.all([
        api("/api/banks"), api("/api/customers"), api("/api/vendors"),
        api("/api/sales-invoices"), api("/api/purchase-invoices"),
      ]);
      const glOptions = banks.accounts.map(b => ({ value: b.gl_account_id, label: `${b.name_ar} (${b.gl_code})` }));
      const partylist = type === "receipt" ? cust.customers : vend.vendors;
      const openInvoices = type === "receipt"
        ? sinv.invoices.filter(i => ["posted", "partially_paid"].includes(i.status) && i.total_cents > i.paid_cents)
        : pinv.invoices.filter(i => ["posted", "partially_paid"].includes(i.status) && i.total_cents > i.paid_cents);
      openModal({
        title: t("payments.new"), wide: true,
        body: `
          <div class="grid cols-3" style="gap:0 16px">
            <div class="field"><span>${ESC(t("payments.type"))}</span>
              <select id="pv-type">
                <option value="receipt" ${type === "receipt" ? "selected" : ""}>${ESC(t("payments.receipt"))}</option>
                <option value="payment" ${type === "payment" ? "selected" : ""}>${ESC(t("payments.payment"))}</option>
              </select></div>
            <div class="field"><span>${ESC(t("common.date"))}</span><input type="date" id="pv-date" value="${today()}"/></div>
            <div class="field"><span>${ESC(L("طريقة الدفع", "Payment method"))}</span>
              <select id="pv-method">
                <option value="cash">${ESC(L("نقدًا", "Cash"))}</option>
                <option value="bank">${ESC(L("تحويل بنكي", "Bank transfer"))}</option>
                <option value="cheque">${ESC(L("شيك", "Cheque"))}</option>
              </select></div>
            <div class="field"><span>${ESC(t("payments.method"))}</span>
              <select id="pv-gl">${glOptions.map(o => `<option value="${o.value}">${ESC(o.label)}</option>`).join("")}</select></div>
            <div class="field"><span>${ESC(t("payments.party"))}</span>
              <select id="pv-party">${partylist.map(p => `<option value="${p.id}" ${preset.party_id === p.id ? "selected" : ""}>${ESC(APP.lang === "ar" ? p.name_ar : p.name_en)}</option>`).join("")}</select></div>
            <div class="field"><span>${ESC(t("common.amount"))} *</span><input id="pv-amount" type="number" step="0.01" min="0.01" value="${preset.amount ? preset.amount.toFixed(2) : ""}"/></div>
            <div class="field"><span>${ESC(t("common.notes"))}</span><input id="pv-notes"/></div>
          </div>
          <div class="section-title" style="margin-top:6px"><span>${ESC(t("payments.allocate"))}</span><span class="line"></span></div>
          <div class="table-wrap" style="border:1px solid var(--border);border-radius:12px;max-height:250px;overflow:auto">
            <table class="data compact" id="pv-alloc">
              <thead><tr><th></th><th>${ESC(t("common.invoice"))}</th><th>${ESC(t("common.date"))}</th>
                <th class="num">${ESC(t("common.remaining"))}</th><th style="width:140px">${ESC(t("common.amount"))}</th></tr></thead>
              <tbody>${openInvoices.map(i => `<tr data-inv="${i.id}" data-rem="${(i.total_cents - i.paid_cents) / 100}">
                <td><input type="checkbox" class="al-chk" ${preset.invoice_id === i.id ? "checked" : ""}/></td>
                <td>${ESC(i.invoice_no)}<br><small class="muted">${ESC(APP.lang === "ar" ? (i.customer_ar || i.vendor_ar) : (i.customer_en || i.vendor_en))}</small></td>
                <td>${fmtDate(i.invoice_date)}</td>
                <td class="num">${fmtMoney(i.total_cents - i.paid_cents)}</td>
                <td><input class="input al-amt" type="number" step="0.01" min="0"
                     value="${preset.invoice_id === i.id ? ((i.total_cents - i.paid_cents) / 100).toFixed(2) : ""}" ${preset.invoice_id === i.id ? "" : "disabled"}/></td>
              </tr>`).join("") || `<tr><td colspan="5" class="empty">${ESC(t("common.empty"))}</td></tr>`}</tbody>
            </table>
          </div>
          <div class="form-error hidden" id="pv-error"></div>`,
        footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
                 <button class="btn btn-primary" id="pv-save">${ESC(L("حفظ وترحيل", "Save & post"))}</button>`,
        onMount: (root, close) => {
          // "نقدًا" selects the cash box automatically; bank/cheque prefer bank accounts
          const accountMeta = banks.accounts.map(b => ({ id: String(b.gl_account_id), kind: b.account_type }));
          const syncMethod = () => {
            const method = $("#pv-method", root).value;
            const wanted = method === "cash" ? "cash" : "bank";
            const match = accountMeta.find(a => a.kind === wanted);
            if (match) $("#pv-gl", root).value = match.id;
          };
          $("#pv-method", root).onchange = syncMethod;
          syncMethod();

          $$(".al-chk", root).forEach(chk => chk.onchange = () => {
            const tr = chk.closest("tr");
            const amt = $(".al-amt", tr);
            amt.disabled = !chk.checked;
            if (chk.checked) {
              amt.value = Number(tr.dataset.rem).toFixed(2);
              const cur = Number($("#pv-amount", root).value || 0);
              $("#pv-amount", root).value = (cur + Number(tr.dataset.rem)).toFixed(2);
            }
          });
          $("#pv-save", root).onclick = async () => {
            try {
              const kind = $("#pv-type", root).value;
              const allocations = [];
              $$("#pv-alloc tbody tr", root).forEach(tr => {
                const chk = $(".al-chk", tr);
                if (chk && chk.checked) allocations.push({
                  invoice_kind: kind === "receipt" ? "sales" : "purchase",
                  invoice_id: Number(tr.dataset.inv),
                  amount: Number($(".al-amt", tr).value || 0),
                });
              });
              await api("/api/payments", {
                method: "POST", body: {
                  payment_type: kind,
                  payment_date: $("#pv-date", root).value,
                  party_type: kind === "receipt" ? "customer" : "vendor",
                  party_id: Number($("#pv-party", root).value),
                  method: $("#pv-method", root).value,
                  gl_account_id: Number($("#pv-gl", root).value),
                  amount: Number($("#pv-amount", root).value || 0),
                  notes: $("#pv-notes", root).value,
                  allocations, post: true,
                }
              });
              toast(t("toast.posted")); close(); render();
            } catch (e) { showErr(root, e.message); }
          };
        }
      });
    }
  };

  /* ==================================================================
     VENDOR PAYABLES
     ================================================================== */
  /* ==================================================================
     DEBT / مديونية — contractor & supplier statements
     Mirrors the paper debt statement: per-account dues billed by
     quantity (طن / نقلة / حاوية / ساعة / إيجار شهري) minus settlement
     vouchers (اذن صرف), plus the consolidated «حساب المديونية» page.
     ================================================================== */
  const DEBT_UNIT_LABEL = (code) => {
    const ar = { ton: "طن", trip: "نقلة", container: "حاوية", hour: "ساعة", month: "شهر", item: "بند" };
    const en = { ton: "Ton", trip: "Trip", container: "Container", hour: "Hour", month: "Month", item: "Item" };
    return APP.lang === "ar" ? (ar[code] || code) : (en[code] || code);
  };
  // Latin digits everywhere, like every other number in the app
  const qtyText = (milli) => (milli ? (milli / 1000).toLocaleString("en-US",
    { maximumFractionDigits: 3 }) : "—");

  async function debtMeta() {
    if (!window.__debtMeta) window.__debtMeta = await api("/api/debt/meta");
    return window.__debtMeta;
  }

  PAGES.debt = {
    perm: "debt.view",
    async render(view, params) {
      if (params && params[0]) return debtStatementPage(view, params[0]);
      setTitle("nav.debt");
      const [meta, data] = await Promise.all([debtMeta(), api("/api/debt/summary")]);
      const T = data.totals;
      const ar = APP.lang === "ar";
      const groupHtml = data.groups.map(g => `
        <div class="section-title"><span>${ESC(ar ? g.label_ar : g.label_en)}</span><span class="line"></span>
          <span class="muted small">${ESC(ar ? "مستحق" : "Due")}: ${fmtMoney(g.due_cents)} ·
            ${ESC(ar ? "مدفوع" : "Paid")}: ${fmtMoney(g.paid_cents)} ·
            <strong>${ESC(ar ? "الصافي" : "Net")}: ${fmtMoney(g.net_cents)}</strong></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code"), render: r => `<span class="mono">${ESC(r.code)}</span>` },
          { key: "name_ar", label: t("debt.account"), cls: "wrap",
            render: r => `<a href="#/debt/${r.id}"><strong>${ESC(ar ? r.name_ar : (r.name_en || r.name_ar))}</strong></a>` },
          { key: "billing_unit", label: t("debt.unit"), render: r => `<span class="badge gray">${ESC(DEBT_UNIT_LABEL(r.billing_unit))}</span>` },
          { key: "items_count", label: t("debt.items"), cls: "num", render: r => `${r.items_count || 0}` },
          { key: "due_cents", label: t("debt.due"), cls: "num", render: r => fmtMoney(r.due_cents) },
          { key: "paid_cents", label: t("debt.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
          { key: "advance_cents", label: t("debt.advance"), cls: "num",
            render: r => r.advance_cents ? `<span class="badge green">${fmtMoney(r.advance_cents)}</span>` : "—" },
          { key: "net_cents", label: t("debt.net"), cls: "num",
            render: r => `<strong class="${r.net_cents < 0 ? "neg" : ""}">${fmtMoney(r.net_cents)}</strong>` },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${can("debt.edit") ? debtAccountEditBtn(r, meta) : ""}
              ${can("debt.delete") ? deleteButton(`/api/debt/accounts/${r.id}`,
                `حساب المديونية ${r.name_ar}`, `debt account ${(r.name_en || r.name_ar)}`) : ""}` },
        ], g.accounts, { compact: true, footer: `<tr class="foot-total">
            <td colspan="4">${ESC((ar ? "إجمالي " : "Total ") + (ar ? g.label_ar : g.label_en))}</td>
            <td class="num">${fmtMoney(g.due_cents)}</td><td class="num">${fmtMoney(g.paid_cents)}</td>
            <td class="num">${g.advance_cents ? fmtMoney(g.advance_cents) : "—"}</td>
            <td class="num">${fmtMoney(g.net_cents)}</td><td></td></tr>` }))}
      `).join("");

      view.innerHTML = `${pageHead("debt.title", "debt.subtitle",
        `<div class="doc-toolbar">
           ${can("debt.create") ? `<button class="btn btn-primary btn-sm" id="debt-new">${icon("plus")} ${ESC(t("debt.newAccount"))}</button>` : ""}
           <button class="btn btn-sm" id="debt-invoices-link">${icon("reports")} ${ESC(t("debt.invoiceStatement"))}</button>
           <button class="btn btn-sm" data-export="/api/debt/summary/xlsx" data-name="debt-summary.xlsx">${icon("chart")} Excel</button>
           <button class="btn btn-sm" data-preview="/api/debt/summary/pdf?lang=${APP.lang}">${icon("printer")} PDF</button>
         </div>`)}
        <div class="grid cols-4">
          ${kpi({ label: t("debt.due"), value: T.due_cents, icon: icon("payables"), tone: "warn" })}
          ${kpi({ label: t("debt.paid"), value: T.paid_cents, icon: icon("check"), tone: "ok" })}
          ${kpi({ label: t("debt.advance"), value: T.advance_cents, icon: icon("bank"),
                  sub: ESC(L("لم تُقابلها فواتير بعد", "Not matched to invoices yet")) })}
          ${kpi({ label: t("debt.net"), value: T.net_cents, icon: icon("reports"), tone: T.net_cents > 0 ? "danger" : "" })}
        </div>
        ${data.groups.length ? groupHtml : `<div class="card"><div class="empty"><span class="big">${icon("debt", "ico-lg")}</span>
            ${ESC(t("common.empty"))}</div></div>`}
        ${card(t("debt.grandTotal"), `
          <div class="stat-line"><span>${ESC(t("debt.due"))}</span><strong>${fmtMoney(T.due_cents)}</strong></div>
          <div class="stat-line"><span>${ESC(t("debt.paid"))}</span><strong>${fmtMoney(T.paid_cents)}</strong></div>
          <div class="stat-line"><span>${ESC(t("debt.advance"))}</span><strong>${fmtMoney(T.advance_cents)}</strong></div>
          <div class="stat-line"><span>${ESC(t("debt.net"))}</span><strong>${fmtMoney(T.net_cents)}</strong></div>
          <p class="field-hint" style="margin-top:8px">${ESC(t("debt.advanceHint"))}</p>
          <p class="field-hint" style="margin-top:8px">${ESC(L(
            "كل سطر مستحق يُرحَّل تلقائيًا: مدين تكلفة الخدمة / دائن الموردون. وكل سداد: مدين الموردون / دائن الصندوق أو البنك.",
            "Every due line posts automatically: Dr cost of service / Cr trade payables. Every settlement: Dr payables / Cr cash or bank."))}</p>`)}
      `;
      if (can("debt.create")) $("#debt-new").onclick = () => debtAccountForm(null, meta);
      $("#debt-invoices-link").onclick = () => { location.hash = "#/debt-invoices"; };
    },
  };

  function debtAccountEditBtn(r, meta) {
    return editButton(`/api/debt/accounts/${r.id}`, `حساب المديونية ${r.name_ar}`,
      `debt account ${r.name_en || r.name_ar}`, [
        { name: "name_ar", label: L("الاسم بالعربية", "Arabic name"), value: r.name_ar, required: true },
        { name: "name_en", label: L("الاسم بالإنجليزية", "English name"), value: r.name_en || "" },
        { name: "kind", label: t("debt.kind"), type: "select", value: r.kind,
          options: meta.kinds.map(k => ({ value: k.code, label: APP.lang === "ar" ? k.label_ar : k.label_en })) },
        { name: "billing_unit", label: t("debt.unit"), type: "select", value: r.billing_unit,
          options: meta.units.map(u => ({ value: u.code, label: APP.lang === "ar" ? `${u.label_ar}` : u.label_en })) },
        { name: "opening_balance", label: t("debt.opening"), type: "number", step: "0.01",
          value: (r.opening_balance_cents / 100).toFixed(2), hint: t("debt.openingHint") },
        { name: "expense_account_id", label: L("حساب المصروف", "Expense account"), type: "select",
          value: r.expense_account_id || "",
          options: meta.expense_accounts.map(a => ({ value: a.id, label: `${a.code} — ${APP.lang === "ar" ? a.name_ar : a.name_en}` })) },
        { name: "notes", label: L("ملاحظات", "Notes"), value: r.notes || "" },
      ]);
  }

  PAGES["debt-invoices"] = {
    perm: "debt.view",
    async render(view) {
      const ar = APP.lang === "ar";
      setTitle("debt.invoiceStatement");
      const [meta, data] = await Promise.all([debtMeta(), api("/api/debt/invoices")]);
      const T = data.totals;
      const rows = data.items || [];
      view.innerHTML = `${pageHead("debt.invoiceStatement", "debt.invoiceStatementSub",
        `<div class="doc-toolbar">
           <a class="btn btn-sm" href="#/debt">${icon("chevron")} ${ESC(t("debt.summary"))}</a>
           <button class="btn btn-sm" data-export="/api/debt/invoices/xlsx" data-name="debt-invoices.xlsx">${icon("chart")} Excel</button>
           <button class="btn btn-sm" data-preview="/api/debt/invoices/pdf?lang=${APP.lang}">${icon("printer")} PDF</button>
         </div>`)}
        <div class="grid cols-3">
          ${kpi({ label: t("debt.rowsCount"), value: T.rows, icon: icon("reports"), raw: true })}
          ${kpi({ label: t("debt.due"), value: T.amount_cents, icon: icon("payables"), tone: "warn" })}
          ${kpi({ label: t("debt.draft"), value: T.draft_cents, icon: icon("journal") })}
        </div>
        <div class="section-title"><span>${ESC(t("debt.invoiceStatement"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "doc_date", label: t("common.date"), render: r => `<span class="mono">${fmtDate(r.doc_date)}</span>` },
          { key: "account_code", label: t("common.code"), render: r => `<span class="mono">${ESC(r.account_code)}</span>` },
          { key: "account_name", label: t("debt.account"), cls: "wrap",
            render: r => `<a href="#/debt/${r.account_id}"><strong>${ESC(r.account_name || "")}</strong></a>` },
          { key: "description", label: t("debt.description"), cls: "wrap", render: r => `
              ${ESC(r.description)}
              <div class="small muted">${[r.operation, r.vessel, r.client_name, r.cargo, r.report_no].filter(Boolean).map(ESC).join(" · ")}</div>` },
          { key: "qty_milli", label: t("debt.qty"), cls: "num",
            render: r => r.qty_milli ? `${qtyText(r.qty_milli)} <small class="muted">${ESC(DEBT_UNIT_LABEL(r.unit))}</small>` : "—" },
          { key: "unit_rate_cents", label: t("debt.rate"), cls: "num",
            render: r => r.unit_rate_cents ? fmtMoney(r.unit_rate_cents) : "—" },
          { key: "amount_cents", label: t("debt.due"), cls: "num", render: r => `<strong>${fmtMoney(r.amount_cents)}</strong>` },
          { key: "item_no", label: L("الرمز التعريفي", "Reference ID"),
            render: r => `<span class="mono small">${ESC(r.item_no)}</span>` },
        ], rows, {
          footer: `<tr class="foot-total"><td colspan="6">${ESC(t("debt.due"))}</td>
            <td class="num">${fmtMoney(T.amount_cents)}</td><td></td></tr>`,
          empty: t("debt.noItems"),
        }))}
      `;
    },
  };

  function debtAccountEditForm(a, meta) {
    openForm({
      title: `${L("تعديل", "Edit")} — ${a.name_ar}`,
      wide: true,
      fields: [
        { name: "name_ar", label: L("الاسم بالعربية", "Arabic name"), value: a.name_ar, required: true },
        { name: "name_en", label: L("الاسم بالإنجليزية", "English name"), value: a.name_en || "" },
        { name: "kind", label: t("debt.kind"), type: "select", value: a.kind,
          options: meta.kinds.map(k => ({ value: k.code, label: APP.lang === "ar" ? k.label_ar : k.label_en })) },
        { name: "billing_unit", label: t("debt.unit"), type: "select", value: a.billing_unit,
          options: meta.units.map(u => ({ value: u.code, label: APP.lang === "ar" ? u.label_ar : u.label_en })) },
        { name: "opening_balance", label: t("debt.opening"), type: "number", step: "0.01",
          value: (a.opening_balance_cents / 100).toFixed(2), hint: t("debt.openingHint") },
        { name: "expense_account_id", label: L("حساب المصروف", "Expense account"), type: "select",
          value: a.expense_account_id || "",
          options: meta.expense_accounts.map(x => ({ value: x.id, label: `${x.code} — ${APP.lang === "ar" ? x.name_ar : x.name_en}` })) },
        { name: "is_active", label: L("الحساب نشط", "Account active"), type: "checkbox", value: !!a.is_active },
        { name: "notes", label: L("ملاحظات", "Notes"), value: a.notes || "", span: 2 },
      ],
      onSubmit: async (data) => {
        const ok = await confirmAsync(
          L(`هل أنت متأكد من تعديل حساب المديونية ${a.name_ar}؟`, `Update the debt account “${a.name_ar}”?`),
          { danger: false, title: L("تأكيد التعديل", "Confirm") });
        if (!ok) throw new Error(L("تم الإلغاء", "Cancelled"));
        data.opening_balance = Number(data.opening_balance || 0);
        data.expense_account_id = data.expense_account_id ? Number(data.expense_account_id) : null;
        data.is_active = data.is_active ? 1 : 0;
        await api(`/api/debt/accounts/${a.id}`, { method: "PATCH", body: data });
        window.__debtMeta = null;
        toast(t("common.saved"));
        setTimeout(() => render(), 30);
      },
    });
  }

  function debtAccountForm(account, meta) {
    openForm({
      title: account ? L("تعديل حساب المديونية", "Edit debt account") : t("debt.newAccount"),
      wide: true,
      fields: [
        { name: "code", label: t("common.code"), value: account ? account.code : "",
          hint: L("اتركه فارغًا للتوليد التلقائي", "Leave empty to auto-number") },
        { name: "name_ar", label: L("الاسم بالعربية", "Arabic name"), required: true, value: account ? account.name_ar : "" },
        { name: "name_en", label: L("الاسم بالإنجليزية", "English name"), value: account ? (account.name_en || "") : "" },
        { name: "kind", label: t("debt.kind"), type: "select", value: account ? account.kind : "contractor",
          options: meta.kinds.map(k => ({ value: k.code, label: APP.lang === "ar" ? k.label_ar : k.label_en })) },
        { name: "billing_unit", label: t("debt.unit"), type: "select", value: account ? account.billing_unit : "item",
          options: meta.units.map(u => ({ value: u.code, label: APP.lang === "ar" ? u.label_ar : u.label_en })) },
        { name: "opening_balance", label: t("debt.opening"), type: "number", step: "0.01", value: "0.00",
          hint: t("debt.openingHint") },
        { name: "expense_account_id", label: L("حساب المصروف", "Expense account"), type: "select",
          value: meta.expense_accounts.find(a => a.code === "5100")?.id ?? "",
          options: meta.expense_accounts.map(a => ({ value: a.id, label: `${a.code} — ${APP.lang === "ar" ? a.name_ar : a.name_en}` })) },
        { name: "vendor_id", label: L("ربط بمورد (اختياري)", "Link a vendor (optional)"), type: "select", value: "",
          options: [{ value: "", label: L("بدون ربط", "Not linked") }, ...meta.vendors.map(v => ({ value: v.id, label: `${v.code} — ${v.name_ar}` }))],
          hint: L("الربط يجعل الرصيد يظهر في حساب المورد أيضًا", "Linking also shows this balance on the vendor account") },
        { name: "notes", label: L("ملاحظات", "Notes"), value: "" },
      ],
      onSubmit: async (data) => {
        const ok = await confirmAsync(L(`هل أنت متأكد من إضافة حساب المديونية ${data.name_ar}؟`,
          `Add the debt account "${data.name_ar}"?`), { danger: false, title: L("تأكيد الإضافة", "Confirm") });
        if (!ok) throw new Error(L("تم الإلغاء", "Cancelled"));
        data.opening_balance = Number(data.opening_balance || 0);
        data.vendor_id = data.vendor_id ? Number(data.vendor_id) : null;
        data.expense_account_id = data.expense_account_id ? Number(data.expense_account_id) : null;
        const res = await api("/api/debt/accounts", { method: "POST", body: data });
        window.__debtMeta = null;
        toast(t("common.saved"));
        location.hash = `#/debt/${res.id}`;
      },
    });
  }

  async function debtStatementPage(view, accountId) {
    const ar = APP.lang === "ar";
    const [meta, st] = await Promise.all([debtMeta(), api(`/api/debt/accounts/${accountId}`)]);
    const a = st.account, T = st.totals;
    setTitle("debt.title", [a.name_ar]);
    const methodLabel = (m) => (meta.methods.find(x => x.code === m) || {}).label_ar || m;
    const statusChip = (s) => s === "posted"
      ? `<span class="badge green">${ESC(t("debt.posted"))}</span>`
      : `<span class="badge gray">${ESC(t("debt.draft"))}</span>`;

    const itemsTable = renderTable([
      { key: "doc_date", label: t("common.date"), render: r => `<span class="mono">${fmtDate(r.doc_date)}</span>` },
      { key: "description", label: t("debt.description"), cls: "wrap", render: r => `
          <strong>${ESC(r.description)}</strong>
          <div class="small muted">${[r.operation, r.vessel, r.client_name, r.cargo,
            (r.period_from ? `${ar ? "من" : "from"} ${fmtDate(r.period_from)} ${ar ? "إلى" : "to"} ${fmtDate(r.period_to || r.period_from)}` : ""),
            (r.report_no ? `${ar ? "بيان تشغيل" : "report"} ${r.report_no}` : "")].filter(Boolean).map(ESC).join(" · ")}</div>` },
      { key: "qty_milli", label: t("debt.qty"), cls: "num",
        render: r => r.qty_milli ? `${qtyText(r.qty_milli)} <small class="muted">${ESC(DEBT_UNIT_LABEL(r.unit))}</small>` : "—" },
      { key: "unit_rate_cents", label: t("debt.rate"), cls: "num", render: r => r.unit_rate_cents ? fmtMoney(r.unit_rate_cents) : "—" },
      { key: "amount_cents", label: t("debt.due"), cls: "num", render: r => `<strong>${fmtMoney(r.amount_cents)}</strong>` },
      { key: "status", label: t("common.status"), render: r => statusChip(r.status) },
      { key: "act", cls: "act", label: t("common.actions"), render: r => `
          ${can("debt.edit") ? debtItemEditBtn(r, meta) : ""}
          ${can("debt.delete") ? deleteButton(`/api/debt/items/${r.id}`,
            `المستحق ${r.description} بمبلغ ${fmtMoney(r.amount_cents)}`,
            `due line ${r.description}`) : ""}` },
    ], st.items, { footer: `<tr class="foot-total"><td colspan="4">${ESC(t("debt.due"))}</td>
        <td class="num">${fmtMoney(T.due_cents)}</td><td></td><td></td></tr>`,
      empty: t("debt.noItems") });

    const paysTable = renderTable([
      { key: "voucher_no", label: t("debt.voucher"), render: r => `<span class="mono">${ESC(r.voucher_no || r.payment_no)}</span>` },
      { key: "pay_date", label: t("common.date"), render: r => `<span class="mono">${fmtDate(r.pay_date)}</span>` },
      { key: "amount_cents", label: t("debt.amount"), cls: "num",
        render: r => `<strong>${fmtMoney(r.amount_cents)}</strong>${r.advance_cents
            ? ` <span class="badge green" title="${ESC(t("debt.advanceHint"))}">${ESC(t("debt.advanceBadge"))}</span>` : ""}` },
      { key: "description", label: t("debt.payDescription"), cls: "wrap",
        render: r => ESC(r.description || (r.advance_cents ? t("debt.advanceBadge") : "—")) },
      { key: "method", label: L("طريقة السداد", "Method"), render: r => ESC(methodLabel(r.method)) },
      { key: "payee", label: t("debt.payee"), render: r => ESC(r.payee || "—") },
      { key: "status", label: t("common.status"), render: r => statusChip(r.status) },
      { key: "act", cls: "act", label: t("common.actions"), render: r => `
          ${can("debt.edit") ? debtPaymentEditBtn(r, meta) : ""}
          ${can("debt.delete") ? deleteButton(`/api/debt/payments/${r.id}`,
            `سداد ${fmtMoney(r.amount_cents)} ${r.voucher_no ? `باذن صرف ${r.voucher_no}` : ""}`,
            `settlement ${fmtMoney(r.amount_cents)}`) : ""}` },
    ], st.payments, { footer: `<tr class="foot-total"><td colspan="2">${ESC(t("debt.paid"))}</td>
        <td class="num">${fmtMoney(T.paid_cents)}</td><td colspan="5"></td></tr>`,
      empty: t("debt.noPayments") });

    view.innerHTML = `${pageHead("debt.statement", "debt.subtitle", `
        <div class="doc-toolbar">
          <a class="btn btn-sm" href="#/debt">${icon("chevron")} ${ESC(t("debt.summary"))}</a>
          <a class="btn btn-sm" href="#/debt-invoices">${icon("reports")} ${ESC(t("debt.invoiceStatement"))}</a>
          ${can("debt.edit") ? `<button class="btn btn-sm" id="debt-edit-acc">${icon("edit")} ${ESC(L("تعديل الحساب", "Edit account"))}</button>` : ""}
          <button class="btn btn-sm" data-export="/api/debt/accounts/${a.id}/statement/xlsx" data-name="statement.xlsx">${icon("chart")} Excel</button>
          <button class="btn btn-sm" data-preview="/api/debt/accounts/${a.id}/statement/pdf?lang=${APP.lang}">${icon("printer")} PDF</button>
          ${can("debt.delete") ? deleteButton(`/api/debt/accounts/${a.id}`, `حساب المديونية ${a.name_ar}`,
            `debt account ${a.name_en || a.name_ar}`, { delBack: "#/debt" }) : ""}
        </div>`)}
      <div class="card" style="margin-bottom:16px">
        <div class="card-body" style="display:flex;flex-wrap:wrap;gap:22px;align-items:center;justify-content:space-between">
          <div>
            <div class="small muted">${ESC(t("debt.account"))}</div>
            <h2 style="margin:2px 0 4px">${ESC(ar ? a.name_ar : (a.name_en || a.name_ar))}</h2>
            <div class="small muted"><span class="mono">${ESC(a.code)}</span> ·
              ${ESC(ar ? a.kind_label_ar : a.kind_label_en)} · ${ESC(t("debt.unit"))}: ${ESC(DEBT_UNIT_LABEL(a.billing_unit))}</div>
          </div>
          <div style="display:flex;gap:26px;flex-wrap:wrap">
            <div><div class="small muted">${ESC(t("debt.due"))}</div><strong style="font-size:18px">${fmtMoney(T.due_cents)}</strong></div>
            <div><div class="small muted">${ESC(t("debt.paid"))}</div><strong style="font-size:18px">${fmtMoney(T.paid_cents)}</strong></div>
            <div><div class="small muted">${ESC(t("debt.advance"))}</div>
              <strong style="font-size:18px;color:${T.advance_cents ? "var(--ok)" : "var(--muted)"}">${fmtMoney(T.advance_cents || 0)}</strong></div>
            <div><div class="small muted">${ESC(t("debt.net"))}</div>
              <strong style="font-size:18px;color:${T.net_cents > 0 ? "var(--danger)" : "var(--ok)"}">${fmtMoney(T.net_cents)}</strong></div>
          </div>
        </div>
      </div>
      <div class="section-title"><span>${ESC(t("debt.items"))}</span><span class="line"></span>
        ${can("debt.create") ? `<button class="btn btn-primary btn-sm" id="debt-new-item">${icon("plus")} ${ESC(t("debt.newItem"))}</button>` : ""}</div>
      ${card("", itemsTable)}
      <div class="section-title"><span>${ESC(t("debt.payments"))}</span><span class="line"></span>
        ${can("debt.create") ? `<button class="btn btn-primary btn-sm" id="debt-new-pay">${icon("plus")} ${ESC(t("debt.newPayment"))}</button>` : ""}</div>
      ${card("", paysTable)}
    `;
    if (can("debt.create")) {
      $("#debt-new-item").onclick = () => debtItemForm(a, meta);
      $("#debt-new-pay").onclick = () => debtPaymentForm(a, meta, null, T);
    }
    if (can("debt.edit")) $("#debt-edit-acc")?.addEventListener("click", () => debtAccountEditForm(a, meta));
  }

  function debtItemForm(a, meta, item) {
    const ar = APP.lang === "ar";
    openForm({
      title: item ? L("تعديل المستحق", "Edit due line") : `${t("debt.newItem")} — ${a.name_ar}`,
      wide: true,
      fields: [
        { name: "doc_date", label: t("common.date"), type: "date", required: true,
          value: item ? item.doc_date : today() },
        { name: "description", label: t("debt.description"), required: true,
          value: item ? item.description : "", span: 2 },
        { name: "operation", label: t("debt.operation"), type: "select", value: item ? (item.operation || "") : "",
          options: [{ value: "", label: L("—", "—") },
                    { value: "شحن", label: L("شحن", "Loading") },
                    { value: "تفريغ", label: L("تفريغ", "Discharge") },
                    { value: "نقل", label: L("نقل", "Transport") },
                    { value: "إيجار", label: L("إيجار", "Hire") },
                    { value: "مشتريات", label: L("مشتريات", "Purchases") }] },
        { name: "unit", label: t("debt.unit"), type: "select", value: item ? item.unit : a.billing_unit,
          options: meta.units.map(u => ({ value: u.code, label: ar ? u.label_ar : u.label_en })) },
        { name: "qty", label: t("debt.qty"), type: "number", step: "0.001",
          value: item && item.qty_milli ? (item.qty_milli / 1000) : "" },
        { name: "unit_rate", label: t("debt.rate"), type: "number", step: "0.01",
          value: item && item.unit_rate_cents ? (item.unit_rate_cents / 100) : "",
          hint: L("الإجمالي = الكمية × الفئة", "Total = quantity × rate") },
        { name: "amount", label: `${t("debt.amount")} ${L("(اختياري)", "(optional)")}`, type: "number", step: "0.01",
          value: item ? "" : "", hint: t("debt.amountHint") },
        { name: "vessel", label: t("debt.vessel"), value: item ? (item.vessel || "") : "" },
        { name: "client_name", label: t("debt.client"), value: item ? (item.client_name || "") : "" },
        { name: "cargo", label: t("debt.cargo"), value: item ? (item.cargo || "") : "" },
        { name: "report_no", label: t("debt.reportNo"), value: item ? (item.report_no || "") : "" },
        { name: "period_from", label: `${t("debt.period")} — ${t("debt.from")}`, type: "date",
          value: item ? (item.period_from || "") : "" },
        { name: "period_to", label: `${t("debt.period")} — ${t("debt.to")}`, type: "date",
          value: item ? (item.period_to || "") : "" },
        { name: "notes", label: L("ملاحظات", "Notes"), value: item ? (item.notes || "") : "", span: 2 },
      ],
      onSubmit: async (data) => {
        const ok = await confirmAsync(
          L(`هل أنت متأكد من ${item ? "تعديل" : "إضافة"} المستحق «${data.description}»؟`,
            `${item ? "Update" : "Add"} the due line “${data.description}”?`),
          { danger: false, title: L("تأكيد", "Confirm") });
        if (!ok) throw new Error(L("تم الإلغاء", "Cancelled"));
        const body = {
          account_id: a.id, doc_date: data.doc_date, description: data.description,
          operation: data.operation || null, unit: data.unit,
          qty: data.qty === "" ? null : Number(data.qty),
          unit_rate: data.unit_rate === "" ? null : Number(data.unit_rate),
          amount: data.amount === "" ? null : Number(data.amount),
          vessel: data.vessel || null, client_name: data.client_name || null, cargo: data.cargo || null,
          report_no: data.report_no || null, period_from: data.period_from || null,
          period_to: data.period_to || null, notes: data.notes || null,
        };
        if (item) await api(`/api/debt/items/${item.id}`, { method: "PATCH", body });
        else await api("/api/debt/items", { method: "POST", body });
        toast(t("common.saved"));
        setTimeout(() => render(), 30);   // stay on the statement, refresh numbers
      },
    });
  }

  function debtItemEditBtn(r, meta) {
    const a = { id: r.account_id, name_ar: r.account_name_ar, billing_unit: r.unit };
    return `<button class="icon-btn edit" data-debt-edit-item="${r.id}" title="${ESC(L("تعديل", "Edit"))}"
        aria-label="${ESC(L("تعديل", "Edit"))}">${icon("edit")}</button>`;
  }

  function debtPaymentForm(a, meta, pay, totals) {
    const ar = APP.lang === "ar";
    const owed = totals ? Math.max(0, totals.net_cents) : null;
    openForm({
      title: pay ? L("تعديل السداد", "Edit settlement") : `${t("debt.newPayment")} — ${a.name_ar}`,
      wide: true,
      fields: [
        { name: "pay_date", label: t("common.date"), type: "date", required: true, value: pay ? pay.pay_date : today() },
        { name: "amount", label: t("debt.amount"), type: "number", step: "0.01", required: true,
          value: pay ? (pay.amount_cents / 100).toFixed(2) : "" },
        { name: "voucher_no", label: t("debt.voucher"), value: pay ? (pay.voucher_no || "") : "",
          hint: L("رقم اذن الصرف الورقي", "The paper voucher number") },
        { name: "method", label: L("طريقة السداد", "Method"), type: "select", value: pay ? pay.method : "cash",
          options: meta.methods.map(m => ({ value: m.code, label: m.label_ar })) },
        { name: "gl_account_id", label: L("الصندوق / البنك", "Cash / bank"), type: "select",
          value: pay ? (pay.gl_account_id || "") : (meta.cash_accounts.find(c => c.code === "1111")?.id ?? ""),
          options: meta.cash_accounts.map(c => ({ value: c.id, label: `${c.code} — ${ar ? c.name_ar : c.name_en}` })) },
        { name: "payee", label: t("debt.payee"), value: pay ? (pay.payee || "") : "" },
        { name: "description", label: t("debt.payDescription"), span: 2,
          value: pay ? (pay.description || "") : "", placeholder: t("debt.payDescriptionPh"),
          hint: owed === 0 ? t("debt.advanceNote")
            : (owed !== null ? `${t("debt.outstandingNow")}: ${fmtMoney(owed)} — ${t("debt.advanceHint")}` : t("debt.advanceHint")) },
        { name: "notes", label: L("ملاحظات", "Notes"), value: pay ? (pay.notes || "") : "", span: 2 },
      ],
      onSubmit: async (data) => {
        const ok = await confirmAsync(
          L(`هل أنت متأكد من ${pay ? "تعديل" : "إضافة"} سداد بمبلغ ${data.amount}؟`,
            `${pay ? "Update" : "Add"} a settlement of ${data.amount}?`),
          { danger: false, title: L("تأكيد", "Confirm") });
        if (!ok) throw new Error(L("تم إلغاء", "Cancelled"));
        const body = {
          account_id: a.id, pay_date: data.pay_date, amount: Number(data.amount),
          voucher_no: data.voucher_no || null, method: data.method,
          gl_account_id: data.gl_account_id ? Number(data.gl_account_id) : null,
          payee: data.payee || null, notes: data.notes || null,
          description: (data.description || "").trim() || null,
        };
        if (pay) await api(`/api/debt/payments/${pay.id}`, { method: "PATCH", body });
        else await api("/api/debt/payments", { method: "POST", body });
        toast(t("common.saved"));
        setTimeout(() => render(), 30);
      },
    });
  }

  function debtPaymentEditBtn(r, meta) {
    return `<button class="icon-btn edit" data-debt-edit-pay="${r.id}" title="${ESC(L("تعديل", "Edit"))}"
        aria-label="${ESC(L("تعديل", "Edit"))}">${icon("edit")}</button>`;
  }

  // the statement page holds the whole record, so the edit modals are opened
  // from the same data the table was rendered from — no second round trip
  document.addEventListener("click", async (ev) => {
    const payBtn = ev.target.closest("[data-debt-edit-pay]");
    const itemBtn = ev.target.closest("[data-debt-edit-item]");
    if (!payBtn && !itemBtn) return;
    ev.preventDefault();
    const route = (location.hash || "").replace(/^#\/?/, "").split("/");
    if (route[0] !== "debt" || !route[1]) return;
    try {
      const [meta, st] = await Promise.all([debtMeta(), api(`/api/debt/accounts/${route[1]}`)]);
      if (itemBtn) {
        const row = st.items.find(x => String(x.id) === itemBtn.dataset.debtEditItem);
        if (row) debtItemForm(st.account, meta, row);
      } else {
        const row = st.payments.find(x => String(x.id) === payBtn.dataset.debtEditPay);
        if (row) debtPaymentForm(st.account, meta, row, st.totals);
      }
    } catch (e) { toast(e.message, "err"); }
  });

  PAGES.payables = {
    perm: "payables.view",
    async render(view) {
      setTitle("nav.payables");
      const data = await api("/api/payables");
      const T = data.totals;
      view.innerHTML = `${pageHead("payables.title", "payables.subtitle",
        `<div class="doc-toolbar">${exportBtn("vendor-payables", { as_of: data.as_of })}
           ${reportExports("vendor-payables", { as_of: data.as_of })}
           <a class="btn btn-sm" href="#/reports?tab=vendor-payables">${icon("reports")} ${ESC(t("reports.vendorPayables"))}</a></div>`)}
        <div class="grid cols-4">
          ${kpi({ label: t("payables.outstanding"), value: T.outstanding_cents, icon: icon("reports"), tone: "warn" })}
          ${kpi({ label: t("payables.overdue"), value: T.overdue_cents, icon: icon("alert"), tone: "danger" })}
          ${kpi({ label: t("payables.invoiced"), value: T.invoiced_cents, icon: icon("grid") })}
          ${kpi({ label: t("payables.paid"), value: T.paid_cents, icon: icon("check"), tone: "ok" })}
        </div>
        <div class="split" style="margin-top:16px">
          ${card(t("payables.aging"), doughnut([
            { label: t("reports.bucket.current"), value: data.aging.current },
            { label: t("reports.bucket.1_30"), value: data.aging["1_30"] },
            { label: t("reports.bucket.31_60"), value: data.aging["31_60"] },
            { label: t("reports.bucket.61_90"), value: data.aging["61_90"] },
            { label: t("reports.bucket.over_90"), value: data.aging.over_90 },
          ]))}
          ${card(t("payables.title"), `
            <div class="stat-line"><span>${ESC(t("payables.journal"))}</span><strong>${fmtMoney(T.journal_balance_cents)}</strong></div>
            <div class="stat-line"><span>${ESC(L("عدد الموردين المدينة", "Vendors with balance"))}</span>
              <strong>${data.vendors.filter(v => v.outstanding_cents > 0).length}</strong></div>
            <div class="stat-line"><span>${ESC(L("أقرب استحقاق", "Earliest due"))}</span>
              <strong>${(data.vendors.filter(v => v.next_due_date).sort((a, b) => a.next_due_date.localeCompare(b.next_due_date))[0] || {}).next_due_date || "—"}</strong></div>
            <p class="field-hint" style="margin-top:8px">${ESC(L("الأرصدة مطابقة لحساب الموردين في الأستاذ العام — الدفعات لا تؤثر إلا بعد الترحيل.",
              "Balances reconcile to the AP control account — payments only count once posted."))}</p>`)}
        </div>
        <div class="section-title"><span>${ESC(t("payables.title"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.vendor"), cls: "wrap", render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong><br><small class="muted">${ESC(r.phone || "")}</small>` },
          { key: "invoiced_cents", label: t("payables.invoiced"), cls: "num", render: r => fmtMoney(r.invoiced_cents) },
          { key: "paid_cents", label: t("payables.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
          { key: "outstanding_cents", label: t("payables.outstanding"), cls: "num", render: r => `<strong>${fmtMoney(r.outstanding_cents)}</strong>` },
          { key: "overdue_cents", label: t("payables.overdue"), cls: "num", render: r => r.overdue_cents ? `<span class="neg">${fmtMoney(r.overdue_cents)}</span>` : "—" },
          { key: "next_due_date", label: t("payables.nextDue"), render: r => r.next_due_date ? fmtDate(r.next_due_date) : "—" },
          { key: "terms", label: t("customers.terms"), render: r => r.payment_terms_days },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `<button class="btn btn-sm btn-ghost" data-stmt="${r.id}">${ESC(t("payables.statement"))}</button>
              ${can("vendors.edit") ? `<button class="icon-btn edit" data-vend-edit="${r.id}"
                  title="${ESC(L("تعديل بيانات المورد والدفعات", "Edit vendor & payments"))}"
                  aria-label="${ESC(L("تعديل", "Edit"))}">${icon("edit")}</button>` : ""}
              ${can("vendors.delete") ? deleteButton(`/api/vendors/${r.id}`, `المورد «${r.name_ar}» (${r.code})`, `vendor “${r.name_en || r.name_ar}” (${r.code})`) : ""}` },
        ], data.vendors, {
          footer: `<tr><td colspan="2">${ESC(t("common.total"))}</td>
            <td class="num">${fmtMoney(T.invoiced_cents)}</td><td class="num">${fmtMoney(T.paid_cents)}</td>
            <td class="num">${fmtMoney(T.outstanding_cents)}</td><td class="num">${fmtMoney(T.overdue_cents)}</td>
            <td colspan="3"></td></tr>`
        }))}
        <p class="field-hint" style="margin-top:10px">${ESC(L("ملاحظة: رصيد الأستاذ لكل مورد يساوي إجمالي الفواتير ناقص المدفوعات المرحّلة، ويمكن تتبعه حتى المستند الأصلي.",
          "Note: each vendor's GL balance equals invoiced minus posted payments and is traceable to source documents."))}</p>`;
      $$("[data-stmt]", view).forEach(b => b.onclick = () => vendorStatement(Number(b.dataset.stmt)));
    }
  };

  async function vendorStatement(vendorId) {
    const d = await api(`/api/vendors/${vendorId}/statement`);
    const v = d.vendor;
    const invoiced = d.invoices.reduce((s, i) => s + i.total_cents, 0);
    const paid = d.payments.reduce((s, p) => s + p.amount_cents, 0);
    openModal({
      title: `${ESC(v.code)} — ${ESC(APP.lang === "ar" ? v.name_ar : v.name_en)}`, wide: true,
      body: `<div class="grid cols-3" style="margin-bottom:14px">
          ${kpi({ label: t("payables.invoiced"), value: invoiced, icon: icon("grid") })}
          ${kpi({ label: t("payables.paid"), value: paid, icon: icon("check"), tone: "ok" })}
          ${kpi({ label: t("payables.outstanding"), value: invoiced - paid, icon: icon("reports"), tone: "warn" })}
        </div>
        <h4 style="margin:6px 0 8px">${ESC(t("nav.purchases"))}</h4>
        ${renderTable([
          { key: "invoice_no", label: L("الرمز التعريفي", "Reference ID") },
          { key: "invoice_date", label: t("common.date") },
          { key: "due_date", label: t("common.dueDate") },
          { key: "total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.total_cents) },
          { key: "paid_cents", label: t("common.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
          { key: "remaining", label: t("common.remaining"), cls: "num", render: r => fmtMoney(r.total_cents - r.paid_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
        ], d.invoices, { compact: true })}
        <h4 style="margin:16px 0 8px">${ESC(t("nav.payments"))}</h4>
        ${renderTable([
          { key: "payment_no", label: L("الرمز التعريفي", "Reference ID") },
          { key: "payment_date", label: t("common.date") },
          { key: "reference", label: t("common.description") },
          { key: "amount_cents", label: t("common.amount"), cls: "num", render: r => fmtMoney(r.amount_cents) },
        ], d.payments, { compact: true })}`
    });
  }

  /* ==================================================================
     RECEIVABLES
     ================================================================== */
  PAGES.receivables = {
    perm: "customers.view",
    async render(view) {
      setTitle("nav.receivables");
      const data = await api("/api/receivables");
      const ag = data.aging, pos = data.position;
      view.innerHTML = `${pageHead("receivables.title", "receivables.subtitle",
        `<div class="doc-toolbar">${exportBtn("aging", { as_of: ag.as_of, type: "sales" })}
           ${reportExports("aging", { as_of: ag.as_of, type: "sales" })}</div>`)}
        <div class="grid cols-4">
          ${kpi({ label: t("dash.ar"), value: pos.total_cents, icon: icon("chart") })}
          ${kpi({ label: t("reports.bucket.current"), value: ag.buckets.current, icon: icon("pie") })}
          ${kpi({ label: t("reports.bucket.1_30"), value: ag.buckets["1_30"], icon: icon("alert"), tone: "warn" })}
          ${kpi({ label: t("reports.bucket.over_90"), value: ag.buckets.over_90, icon: icon("lock"), tone: "danger" })}
        </div>
        <div class="section-title"><span>${ESC(t("customers.title"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.customer"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong>` },
          { key: "outstanding_cents", label: t("payables.outstanding"), cls: "num", render: r => fmtMoney(r.outstanding_cents) },
          { key: "credit_limit_cents", label: t("sales.creditLimit"), cls: "num", render: r => fmtMoney(r.credit_limit_cents) },
          { key: "utilization", label: L("نسبة الاستخدام", "Utilization"), render: r => {
              const p = r.credit_limit_cents ? Math.min(100, r.outstanding_cents / r.credit_limit_cents * 100) : 0;
              return `<div class="progress ${p > 90 ? "danger" : p > 70 ? "warn" : ""}"><span style="width:${p}%"></span></div>
                      <small class="muted">${p.toFixed(0)}%</small>`;
            } },
          { key: "status", label: t("common.status"), render: r => r.outstanding_cents > r.credit_limit_cents && r.credit_limit_cents ? `<span class="badge red">${ESC(L("تجاوز الحد", "Over limit"))}</span>` : `<span class="badge green">${ESC(L("داخل الحد", "Within limit"))}</span>` },
        ], pos.customers))}
        <div class="section-title"><span>${ESC(t("reports.agingAR"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "invoice_no", label: L("الرمز التعريفي", "Reference ID") },
          { key: "party_ar", label: t("common.customer"), cls: "wrap" },
          { key: "due_date", label: t("common.dueDate") },
          { key: "remaining_cents", label: t("common.remaining"), cls: "num", render: r => fmtMoney(r.remaining_cents) },
          { key: "days_overdue", label: t("reports.daysOverdue"), cls: "num", render: r => r.days_overdue > 0 ? `<span class="neg">${r.days_overdue}</span>` : "—" },
          { key: "bucket", label: t("payables.aging"), render: r => `<span class="badge ${r.bucket === "current" ? "green" : r.bucket === "over_90" ? "red" : "amber"}">${ESC(t("reports.bucket." + r.bucket))}</span>` },
        ], ag.items))}`;
    }
  };

  /* ==================================================================
     CUSTOMERS / VENDORS
     ================================================================== */
  PAGES.customers = {
    perm: "customers.view",
    async render(view) {
      setTitle("nav.customers");
      const data = await api("/api/customers");
      view.innerHTML = `${pageHead("customers.title", "nav.masters",
        can("customers.create") ? `<button class="btn btn-primary btn-sm" id="new-cust">${icon("plus")} ${ESC(t("customers.new"))}</button>` : "")}
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.name"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong>` },
          { key: "phone", label: t("customers.phone"), render: r => ESC(r.phone || "—") },
          { key: "payment_terms_days", label: t("customers.terms") },
          { key: "credit_limit_cents", label: t("sales.creditLimit"), cls: "num", render: r => fmtMoney(r.credit_limit_cents) },
          { key: "outstanding_cents", label: t("payables.outstanding"), cls: "num", render: r => fmtMoney(r.outstanding_cents) },
          { key: "is_active", label: t("common.status"), render: r => statusBadge(r.is_active ? "active" : "inactive") },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${can("customers.edit") ? editButton(`/api/customers/${r.id}`,
                  `العميل «${r.name_ar}» (${r.code})`, `customer “${r.name_en}” (${r.code})`, [
                    { name: "code", label: t("common.code"), required: true, value: r.code },
                    { name: "name_ar", label: "الاسم بالعربية", required: true, value: r.name_ar },
                    { name: "name_en", label: "Name (English)", required: true, value: r.name_en },
                    { name: "phone", label: t("customers.phone"), value: r.phone || "" },
                    { name: "email", label: L("البريد الإلكتروني", "Email"), value: r.email || "" },
                    { name: "tax_number", label: L("الرقم الضريبي", "Tax number"), value: r.tax_number || "" },
                    { name: "credit_limit", label: t("sales.creditLimit"), type: "number", step: "0.01", value: (r.credit_limit_cents || 0) / 100 },
                    { name: "payment_terms_days", label: t("customers.terms"), type: "number", value: r.payment_terms_days ?? 30 },
                    { name: "is_active", label: t("common.status"), type: "select", value: r.is_active ? "1" : "0",
                      options: [{ value: "1", label: L("نشط", "Active") }, { value: "0", label: L("موقوف", "Inactive") }] },
                  ]) : ""}
              ${can("customers.delete")
                ? deleteButton(`/api/customers/${r.id}`, `العميل «${r.name_ar}» (${r.code})`, `customer “${r.name_en}” (${r.code})`)
                : "" }` },
        ], data.customers))}`;
      if (can("customers.create")) $("#new-cust").onclick = () => openForm({
        title: t("customers.new"),
        fields: [
          { name: "code", label: t("common.code"), required: true, value: "C-" + Math.floor(1000 + Math.random() * 9000) },
          { name: "name_ar", label: "الاسم بالعربية", required: true },
          { name: "name_en", label: "Name (English)", required: true },
          { name: "phone", label: t("customers.phone") },
          { name: "credit_limit", label: t("sales.creditLimit"), type: "number", step: "0.01", value: "0" },
          { name: "payment_terms_days", label: t("customers.terms"), type: "number", value: "30" },
        ],
        onSubmit: async (d) => { await api("/api/customers", { method: "POST", body: d }); toast(t("common.saved")); render(); }
      });
    }
  };

  PAGES.vendors = {
    perm: "vendors.view",
    async render(view) {
      setTitle("nav.vendors");
      const data = await api("/api/vendors");
      view.innerHTML = `${pageHead("vendors.title", "nav.masters",
        can("vendors.create") ? `<button class="btn btn-primary btn-sm" id="new-vend">${icon("plus")} ${ESC(t("vendors.new"))}</button>` : "")}
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.name"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong>` },
          { key: "phone", label: t("customers.phone"), render: r => ESC(r.phone || "—") },
          { key: "payment_terms_days", label: t("customers.terms") },
          { key: "outstanding_cents", label: t("payables.outstanding"), cls: "num", render: r => fmtMoney(r.outstanding_cents) },
          { key: "is_active", label: t("common.status"), render: r => statusBadge(r.is_active ? "active" : "inactive") },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `<button class="btn btn-sm btn-ghost" data-stmt="${r.id}">${ESC(t("payables.statement"))}</button>
              ${can("vendors.edit") ? `<button class="icon-btn edit" data-vend-edit="${r.id}"
                  title="${ESC(L("تعديل بيانات المورد والدفعات", "Edit vendor & payments"))}"
                  aria-label="${ESC(L("تعديل", "Edit"))}">${icon("edit")}</button>` : ""}
              ${can("vendors.delete") ? deleteButton(`/api/vendors/${r.id}`, `المورد «${r.name_ar}» (${r.code})`, `vendor “${r.name_en || r.name_ar}” (${r.code})`) : ""}` },
        ], data.vendors))}`;
      $$("[data-stmt]", view).forEach(b => b.onclick = () => vendorStatement(Number(b.dataset.stmt)));
      if (can("vendors.create")) $("#new-vend").onclick = () => openForm({
        title: t("vendors.new"),
        fields: [
          { name: "code", label: t("common.code"), required: true, value: "V-" + Math.floor(2000 + Math.random() * 900) },
          { name: "name_ar", label: "الاسم بالعربية", required: true },
          { name: "name_en", label: "Name (English)", hint: L("اختياري — يُستخدم الاسم العربي إن تُرك فارغًا", "Optional — falls back to the Arabic name") },
          { name: "phone", label: t("customers.phone") },
          { name: "payment_terms_days", label: t("customers.terms"), type: "number", value: "30" },
        ],
        onSubmit: async (d) => { await api("/api/vendors", { method: "POST", body: d }); toast(t("common.saved")); render(); }
      });
    }
  };

  /* ------------------------------------------------------------------
     VENDOR PANEL — edit the vendor, see its balance, manage its payments
     ------------------------------------------------------------------ */
  PAGES.vendors.panel = async function (id) {
    const ar = APP.lang === "ar";
    const d = await api(`/api/vendors/${id}`);
    const v = d.vendor;
    const subject = ar ? `المورد «${v.name_ar}» (${v.code})` : `vendor “${v.name_en || v.name_ar}” (${v.code})`;
    const original = {
      code: v.code, name_ar: v.name_ar, name_en: v.name_en || "",
      phone: v.phone || "", email: v.email || "", tax_number: v.tax_number || "",
      address: v.address || "", payment_terms_days: v.payment_terms_days ?? 30,
      is_active: v.is_active ? "1" : "0",
    };
    const fld = (name, label, value, o = {}) => `<div class="field" style="${o.span ? "grid-column:span 2" : ""};margin-bottom:12px">
        <span>${ESC(label)}${o.required ? " *" : ""}</span>
        <input id="vp-${name}" type="${o.type || "text"}" value="${ESC(value ?? "")}"
          ${o.readonly ? "readonly style=\"background:var(--surface-3);font-weight:700\"" : ""}
          ${o.step ? `step="${o.step}"` : ""}/>
        ${o.hint ? `<div class="field-hint">${ESC(o.hint)}</div>` : ""}</div>`;

    const payRows = d.payments || [];
    const body = `
      <div class="tabs" id="vp-tabs">
        <button type="button" class="tab active" data-tab="data">${ESC(ar ? "بيانات المورد" : "Vendor details")}</button>
        <button type="button" class="tab" data-tab="pay">${ESC(ar ? `الدفعات (${payRows.length})` : `Payments (${payRows.length})`)}</button>
      </div>

      <div id="vp-pane-data">
        <div class="grid cols-2" style="gap:0 16px">
          ${fld("code", t("common.code"), original.code, { required: true })}
          <div class="field" style="margin-bottom:12px">
            <span>${ESC(ar ? "رصيد المستحق" : "Outstanding balance")}</span>
            <div class="readonly-money ${d.outstanding_cents > 0 ? "neg" : "pos"}">
              ${fmtMoney(d.outstanding_cents || 0)}
            </div>
            <div class="field-hint">${ESC(ar ? "المتبقي على المورد من الفواتير المرحّلة — يتحدّث تلقائيًا مع الدفعات"
                                              : "Remaining on posted invoices — updated automatically by payments")}</div>
          </div>
          ${fld("name_ar", ar ? "الاسم بالعربية" : "Arabic name", original.name_ar, { required: true })}
          ${fld("name_en", "Name (English)", original.name_en,
                { hint: ar ? "اختياري — يُستخدم الاسم العربي إن تُرك فارغًا" : "Optional — falls back to the Arabic name" })}
          ${fld("phone", t("customers.phone"), original.phone)}
          ${fld("email", ar ? "البريد الإلكتروني" : "Email", original.email, { type: "email" })}
          ${fld("tax_number", ar ? "الرقم الضريبي" : "Tax number", original.tax_number)}
          ${fld("address", ar ? "العنوان" : "Address", original.address)}
          ${fld("payment_terms_days", t("customers.terms"), original.payment_terms_days, { type: "number" })}
          <div class="field" style="margin-bottom:12px"><span>${ESC(t("common.status"))}</span>
            <select id="vp-is_active">
              <option value="1" ${original.is_active === "1" ? "selected" : ""}>${ESC(ar ? "نشط" : "Active")}</option>
              <option value="0" ${original.is_active === "0" ? "selected" : ""}>${ESC(ar ? "موقوف" : "Inactive")}</option>
            </select></div>
        </div>
        <div class="form-error hidden" id="gen-error"></div>
      </div>

      <div id="vp-pane-pay" class="hidden">
        <div class="grid cols-3" style="margin-bottom:14px">
          ${kpi({ label: ar ? "إجمالي المستحق" : "Outstanding", value: d.outstanding_cents || 0, icon: icon("alert"), tone: (d.outstanding_cents || 0) > 0 ? "warn" : "ok" })}
          ${kpi({ label: ar ? "إجمالي المدفوع" : "Paid to date", value: d.paid_total_cents || 0, icon: icon("coins") })}
          ${kpi({ label: ar ? "عدد الدفعات" : "Payments", value: payRows.length, icon: icon("payments"), raw: true })}
        </div>
        <div class="section-title"><span>${ESC(ar ? "دفعات المورد" : "Vendor payments")}</span><span class="line"></span>
          ${can("payments.create") ? `<button class="btn btn-primary btn-sm" id="vp-newpay">${icon("plus")} ${ESC(ar ? "تسجيل دفعة" : "Record a payment")}</button>` : ""}</div>
        ${renderTable([
          { key: "payment_date", label: ar ? "تاريخ الدفعة" : "Payment date", render: r => fmtDate(r.payment_date) },
          { key: "payment_no", label: ar ? "الرمز التعريفي" : "Reference ID", render: r => `<strong>${ESC(r.payment_no)}</strong>` },
          { key: "amount_cents", label: t("common.amount"), cls: "num", render: r => fmtMoney(r.amount_cents) },
          { key: "allocated_cents", label: ar ? "المخصّص للفواتير" : "Allocated", cls: "num", render: r => fmtMoney(r.allocated_cents || 0) },
          { key: "unallocated_cents", label: ar ? "غير مخصّص" : "Unallocated", cls: "num",
            render: r => (r.unallocated_cents || 0) ? `<span class="neg">${fmtMoney(r.unallocated_cents)}</span>` : "—" },
          { key: "method", label: ar ? "الطريقة" : "Method", render: r => ESC(r.method || "—") },
          { key: "reference", label: ar ? "المرجع" : "Reference", render: r => ESC(r.reference || "—") },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `
              ${can("payments.edit") ? `<button class="icon-btn edit" data-pay-edit="${r.id}"
                  title="${ESC(L("تعديل السند", "Edit voucher"))}" aria-label="${ESC(L("تعديل", "Edit"))}">${icon("edit")}</button>` : ""}
              ${can("payments.delete") ? deleteButton(`/api/payments/${r.id}`,
                  `${r.payment_type === "receipt" ? "سند التحصيل" : "سند الدفع"} ${r.payment_no}`,
                  `${r.payment_type === "receipt" ? "receipt" : "payment"} ${r.payment_no}`,
                  { delBack: `#/vendors` }) : ""}` },
        ], payRows, { compact: true, empty: ar ? "لا توجد دفعات مسجّلة لهذا المورد بعد" : "No payments recorded for this vendor yet" })}
        <div class="section-title"><span>${ESC(ar ? "فواتير المشتريات" : "Purchase invoices")}</span><span class="line"></span></div>
        ${renderTable([
          { key: "invoice_no", label: t("common.invoice"), render: r => `<strong>${ESC(r.invoice_no)}</strong>` },
          { key: "invoice_date", label: t("common.date"), render: r => fmtDate(r.invoice_date) },
          { key: "due_date", label: t("common.dueDate"), render: r => fmtDate(r.due_date) },
          { key: "total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.total_cents) },
          { key: "paid_cents", label: t("common.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
          { key: "remaining_cents", label: t("common.remaining"), cls: "num",
            render: r => `<span class="${r.remaining_cents ? "neg" : ""}">${fmtMoney(r.remaining_cents)}</span>` },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
        ], d.invoices || [], { compact: true })}
      </div>`;

    openModal({
      title: (ar ? "تعديل " : "Edit ") + subject,
      body, wide: true,
      footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
               <button class="btn btn-primary" id="vp-save">${ESC(ar ? "حفظ التعديل" : "Save changes")}</button>`,
      onMount: (root, close) => {
        $$("#vp-tabs .tab", root).forEach(tab => tab.onclick = () => {
          $$("#vp-tabs .tab", root).forEach(x => x.classList.toggle("active", x === tab));
          $("#vp-pane-data", root).classList.toggle("hidden", tab.dataset.tab !== "data");
          $("#vp-pane-pay", root).classList.toggle("hidden", tab.dataset.tab !== "pay");
        });

        const newPay = $("#vp-newpay", root);
        if (newPay) newPay.onclick = async () => {
          close();
          await PAGES.payments.newVoucher("payment", { party_id: v.id });
        };

        $("#vp-save", root).onclick = async () => {
          const data = {
            code: $("#vp-code", root).value.trim(),
            name_ar: $("#vp-name_ar", root).value.trim(),
            name_en: $("#vp-name_en", root).value.trim(),
            phone: $("#vp-phone", root).value.trim(),
            email: $("#vp-email", root).value.trim(),
            tax_number: $("#vp-tax_number", root).value.trim(),
            address: $("#vp-address", root).value.trim(),
            payment_terms_days: Number($("#vp-payment_terms_days", root).value || 0),
            is_active: $("#vp-is_active", root).value,
          };
          if (!data.code || !data.name_ar) {
            showErr(root, ar ? "الكود والاسم بالعربية مطلوبان" : "Code and Arabic name are required");
            return;
          }
          const changed = Object.keys(data).filter(k => String(data[k]) !== String(original[k]));
          if (!changed.length) { toast(ar ? "لا يوجد تغيير لحفظه" : "Nothing changed", "err"); return; }
          const ok = await confirmAsync(
            ar ? `هل أنت متأكد من تعديل ${subject}؟\nسيُسجَّل التعديل في سجل التدقيق باسمك ووقته.`
               : `Are you sure you want to edit ${subject}?\nThe change is recorded in the audit trail.`,
            { title: ar ? "تأكيد التعديل" : "Confirm edit" });
          if (!ok) return;
          const btn = $("#vp-save", root);
          btn.disabled = true;
          try {
            await api(`/api/vendors/${v.id}`, { method: "PATCH", body: data });
            toast(ar ? "تم حفظ التعديل" : "Changes saved");
            close();
            await render();
          } catch (e) {
            showErr(root, e.message);
            btn.disabled = false;
          }
        };
      },
    });
  };

  // one delegated handler for every "edit voucher" button (payments page + vendor panel)
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-pay-edit]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    PAGES.payments.editForm(Number(btn.dataset.payEdit), async () => {
      const root = $("#modal-root");
      if (root && !root.classList.contains("hidden")) { root.classList.add("hidden"); root.innerHTML = ""; }
      await render();
    });
  });

  // one delegated handler for every vendor row button
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-vend-edit]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    PAGES.vendors.panel(Number(btn.dataset.vendEdit));
  });

  /* ==================================================================
     INVENTORY
     ================================================================== */
  PAGES.inventory = {
    perm: "inventory.view",
    async render(view) {
      setTitle("nav.inventory");
      const data = await api("/api/inventory");
      const stock = data.stock.filter(r => r.qty_milli !== 0 || r.value_cents !== 0);
      view.innerHTML = `${pageHead("inventory.title", "inventory.subtitle",
        can("inventory.manage") ? `<button class="btn btn-primary btn-sm" id="new-move">${icon("plus")} ${ESC(t("inventory.newMovement"))}</button>` : "")}
        <div class="grid cols-3">
          ${kpi({ label: t("inventory.value"), value: data.totals.value_cents, icon: icon("grid") })}
          ${kpi({ label: L("عدد الأصناف", "SKUs"), value: new Set(stock.map(s => s.product_id)).size, icon: icon("grid"), raw: true })}
          ${kpi({ label: L("أصناف تحت حد إعادة الطلب", "Below reorder level"), value: stock.filter(s => s.reorder_level_milli && s.qty_milli <= s.reorder_level_milli).length, icon: icon("alert"), tone: "warn", raw: true })}
        </div>
        <div class="section-title"><span>${ESC(t("inventory.stock"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "sku", label: t("common.code") },
          { key: "name_ar", label: L("الصنف", "Product"), render: r => ESC(APP.lang === "ar" ? r.name_ar : r.name_en) },
          { key: "warehouse_ar", label: t("common.warehouse") },
          { key: "qty_milli", label: t("common.qty"), cls: "num", render: r => {
              const low = r.reorder_level_milli && r.qty_milli <= r.reorder_level_milli;
              return `<span class="${low ? "neg" : ""}">${fmtNum(r.qty_milli / 1000, 0)}</span>`;
            } },
          { key: "reorder_level_milli", label: t("inventory.reorder"), cls: "num", render: r => fmtNum(r.reorder_level_milli / 1000, 0) },
          { key: "value_cents", label: t("inventory.value"), cls: "num", render: r => fmtMoney(r.value_cents) },
          { key: "avg", label: t("inventory.unitCost"), cls: "num", render: r => r.qty_milli ? fmtMoney(Math.round(r.value_cents * 1000 / r.qty_milli)) : "—" },
          { key: "act", cls: "act", label: t("common.actions"), render: r => can("inventory.manage")
              ? editButton(`/api/products/${r.product_id}`, `الصنف «${r.name_ar}» (${r.sku})`, `product “${r.name_en || r.name_ar}” (${r.sku})`, [
                  { name: "sku", label: t("common.code"), required: true, value: r.sku },
                  { name: "name_ar", label: "الاسم بالعربية", required: true, value: r.name_ar },
                  { name: "name_en", label: "Name (English)", required: true, value: r.name_en || r.name_ar },
                  { name: "unit", label: L("الوحدة", "Unit"), value: r.unit || "" },
                  { name: "reorder_level", label: t("inventory.reorder"), type: "number", value: (r.reorder_level_milli || 0) / 1000 },
                ])
              : "—" },
        ], stock))}
        <div class="section-title"><span>${ESC(t("inventory.movements"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "move_date", label: t("common.date") },
          { key: "sku", label: t("common.code") },
          { key: "product_ar", label: L("الصنف", "Product") },
          { key: "warehouse_ar", label: t("common.warehouse") },
          { key: "move_type", label: t("inventory.moveType"), render: r => `<span class="badge ${["in", "transfer_in"].includes(r.move_type) ? "green" : r.move_type === "out" ? "amber" : "gray"}">${ESC(r.move_type)}</span>` },
          { key: "qty_milli", label: t("common.qty"), cls: "num", render: r => fmtNum(r.qty_milli / 1000, 0) },
          { key: "unit_cost_cents", label: t("inventory.unitCost"), cls: "num", render: r => fmtMoney(r.unit_cost_cents) },
          { key: "value_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.value_cents) },
          { key: "ref_type", label: t("common.description"), render: r => ESC(r.ref_type || r.notes || "—") },
        ], data.movements, { compact: true }))}`;

      if (can("inventory.manage")) $("#new-move").onclick = async () => {
        const [prods, meta] = await Promise.all([api("/api/products"), api("/api/meta")]);
        openForm({
          title: t("inventory.newMovement"),
          fields: [
            { name: "move_type", label: t("inventory.moveType"), type: "select", required: true,
              options: [{ value: "in", label: t("inventory.in") }, { value: "out", label: t("inventory.out") },
                        { value: "adjust", label: t("inventory.adjust") }, { value: "transfer", label: t("inventory.transfer") }] },
            { name: "product_id", label: L("الصنف", "Product"), type: "select", required: true,
              options: prods.products.map(p => ({ value: p.id, label: `${p.sku} — ${p.name_ar}` })) },
            { name: "warehouse_id", label: t("common.warehouse"), type: "select", required: true,
              options: meta.warehouses.map(w => ({ value: w.id, label: w.name_ar })) },
            { name: "target_warehouse_id", label: L("المستودع المستهدف (للتحويل)", "Target warehouse (transfer)"), type: "select",
              options: [{ value: "", label: "—" }, ...meta.warehouses.map(w => ({ value: w.id, label: w.name_ar }))] },
            { name: "qty", label: t("common.qty"), type: "number", step: "0.001", required: true },
            { name: "unit_cost", label: t("inventory.unitCost"), type: "number", step: "0.01" },
            { name: "move_date", label: t("common.date"), type: "date", value: today() },
            { name: "notes", label: t("common.notes") },
          ],
          onSubmit: async (d) => { await api("/api/inventory/movements", { method: "POST", body: d }); toast(t("common.saved")); render(); }
        });
      };
    }
  };

  /* ==================================================================
     BANKS
     ================================================================== */
  PAGES.banks = {
    perm: "banks.view",
    async render(view) {
      setTitle("nav.banks");
      const [banks, txns] = await Promise.all([api("/api/banks"), api("/api/bank-transactions")]);
      const unreconciled = txns.transactions.filter(t => !t.is_reconciled);
      view.innerHTML = `${pageHead("banks.title", "banks.subtitle",
        can("banks.manage") ? `<button class="btn btn-primary btn-sm" id="new-txn">${icon("plus")} ${ESC(t("banks.newTransaction"))}</button>
          <button class="btn btn-sm" id="new-bank">${icon("plus")} ${ESC(L("حساب بنكي / صندوق", "Bank / cash account"))}</button>` : "")}
        <div class="grid cols-3">
          ${banks.position.accounts.map(a => kpi({ label: APP.lang === "ar" ? a.name_ar : (a.name_en || a.name_ar), value: a.balance_cents, icon: a.account_type === "cash" ? icon("coins") : icon("banks"),
            sub: `${a.account_type === "cash" ? t("payments.cash") : t("payments.bank")} · ${a.gl_code}` })).join("")}
        </div>
        <div class="section-title"><span>${ESC(L("البنوك والصناديق", "Banks & cash boxes"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name", label: t("common.name"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : (r.name_en || r.name_ar))}</strong>` },
          { key: "kind", label: L("النوع", "Type"), render: r => `<span class="badge ${r.account_type === "cash" ? "amber" : "blue"}">${ESC(r.account_type === "cash" ? t("payments.cash") : t("payments.bank"))}</span>` },
          { key: "gl_code", label: t("common.account"), render: r => `<span class="mono">${ESC(r.gl_code)}</span>` },
          { key: "currency", label: L("العملة", "Currency") },
          { key: "balance_cents", label: t("common.balance"), cls: "num", render: r => fmtMoney(r.balance_cents) },
          { key: "act", cls: "act", label: t("common.actions"), render: r => can("banks.manage") ? `
              ${editButton(`/api/bank-accounts/${r.id}`,
                  `${r.account_type === "cash" ? "الصندوق" : "الحساب البنكي"} «${r.name_ar}»`,
                  `${r.account_type === "cash" ? "cash box" : "bank account"} “${r.name_en || r.name_ar}”`, [
                    { name: "code", label: t("common.code"), required: true, value: r.code },
                    { name: "name_ar", label: "الاسم بالعربية", required: true, value: r.name_ar },
                    { name: "name_en", label: "Name (English)", required: true, value: r.name_en || r.name_ar },
                    { name: "account_type", label: L("النوع", "Type"), type: "select", value: r.account_type,
                      options: [{ value: "bank", label: t("payments.bank") }, { value: "cash", label: t("payments.cash") }] },
                    { name: "iban", label: "IBAN", value: r.iban || "" },
                    { name: "currency", label: L("العملة", "Currency"), value: r.currency || "EGP" },
                    { name: "is_active", label: t("common.status"), type: "select", value: (r.is_active === 0 ? "0" : "1"),
                      options: [{ value: "1", label: L("نشط", "Active") }, { value: "0", label: L("موقوف", "Inactive") }] },
                  ])}
              ${deleteButton(`/api/bank-accounts/${r.id}`, `${r.account_type === "cash" ? "الصندوق" : "الحساب البنكي"} «${r.name_ar}»`, `${r.account_type === "cash" ? "cash box" : "bank account"} “${r.name_en || r.name_ar}”`)}`
              : "" },
        ], banks.position.accounts))}
        <div class="section-title"><span>${ESC(t("banks.transactions"))}</span><span class="line"></span>
          <span class="small muted">${unreconciled.length} ${ESC(t("banks.unreconciled"))}</span></div>
        ${card("", renderTable([
          { key: "txn_date", label: t("common.date") },
          { key: "bank_name", label: t("common.account") },
          { key: "description", label: t("common.description"), render: r => ESC(r.description) },
          { key: "statement_ref", label: L("مرجع", "Ref"), render: r => ESC(r.statement_ref || "—") },
          { key: "debit_cents", label: t("common.debit"), cls: "num", render: r => r.debit_cents ? fmtMoney(r.debit_cents) : "" },
          { key: "credit_cents", label: t("common.credit"), cls: "num", render: r => r.credit_cents ? fmtMoney(r.credit_cents) : "" },
          { key: "matched_payment_no", label: t("payments.receipt"), render: r => ESC(r.matched_payment_no || "—") },
          { key: "is_reconciled", label: t("common.status"), render: r => r.is_reconciled ? `<span class="badge green">${ESC(t("banks.reconciled"))}</span>` : `<span class="badge amber">${ESC(t("banks.unreconciled"))}</span>` },
          { key: "act", cls: "act", label: t("common.actions"), render: r => can("banks.manage") ? `
              ${r.is_reconciled
                ? `<button class="btn btn-sm btn-ghost" data-unrec="${r.id}">${ESC(L("إلغاء التسوية", "Unreconcile"))}</button>`
                : `<button class="btn btn-sm btn-ghost" data-rec="${r.id}">${ESC(t("banks.reconcile"))}</button>`}
              ${!r.is_reconciled ? editButton(`/api/bank-transactions/${r.id}`,
                  `الحركة البنكية «${r.description}»`, `bank transaction “${r.description}”`, [
                    { name: "txn_date", label: t("common.date"), type: "date", value: r.txn_date },
                    { name: "description", label: t("common.description"), required: true, value: r.description },
                    { name: "statement_ref", label: L("مرجع كشف الحساب", "Statement ref"), value: r.statement_ref || "" },
                    { name: "debit", label: L("مدين (إيداع)", "Debit (deposit)"), type: "number", step: "0.01", value: (r.debit_cents || 0) / 100 },
                    { name: "credit", label: L("دائن (سحب)", "Credit (withdrawal)"), type: "number", step: "0.01", value: (r.credit_cents || 0) / 100 },
                  ]) : ""}
              ${!r.is_reconciled ? deleteButton(`/api/bank-transactions/${r.id}`, `الحركة البنكية «${r.description}»`, `bank transaction “${r.description}”`) : ""}`
              : "" },
        ], txns.transactions))}`;

      if (can("banks.manage")) {
        $("#new-bank").onclick = async () => {
          const acc = await api("/api/accounts");
          const assets = acc.accounts.filter(a => a.is_postable && a.type === "asset" && a.is_active !== 0);
          openForm({
            title: L("حساب بنكي / صندوق جديد", "New bank / cash account"),
            fields: [
              { name: "code", label: t("common.code"), required: true, value: "BNK-" + Math.floor(10 + Math.random() * 89) },
              { name: "name_ar", label: "الاسم بالعربية", required: true },
              { name: "name_en", label: "Name (English)", required: true },
              { name: "account_type", label: L("النوع", "Type"), type: "select",
                options: [{ value: "bank", label: t("payments.bank") }, { value: "cash", label: t("payments.cash") }] },
              { name: "gl_account_id", label: L("حساب الأستاذ", "GL account"), type: "select", required: true,
                options: assets.map(a => ({ value: a.id, label: `${a.code} — ${APP.lang === "ar" ? a.name_ar : (a.name_en || a.name_ar)}` })) },
              { name: "iban", label: "IBAN" },
              { name: "currency", label: L("العملة", "Currency"), value: "EGP" },
            ],
            onSubmit: async (d) => { await api("/api/bank-accounts", { method: "POST", body: d }); toast(t("common.saved")); render(); }
          });
        };
        $("#new-txn").onclick = () => openForm({
          title: t("banks.newTransaction"),
          fields: [
            { name: "bank_account_id", label: t("common.account"), type: "select", required: true,
              options: banks.accounts.map(b => ({ value: b.id, label: b.name_ar })) },
            { name: "txn_date", label: t("common.date"), type: "date", value: today(), required: true },
            { name: "description", label: t("common.description"), required: true },
            { name: "direction", label: L("الاتجاه", "Direction"), type: "select",
              options: [{ value: "debit", label: L("إيداع", "Deposit") }, { value: "credit", label: L("سحب", "Withdrawal") }] },
            { name: "amount", label: t("common.amount"), type: "number", step: "0.01", required: true },
            { name: "statement_ref", label: L("مرجع كشف الحساب", "Statement ref") },
          ],
          onSubmit: async (d) => { await api("/api/bank-transactions", { method: "POST", body: d }); toast(t("common.saved")); render(); }
        });
        $$("[data-unrec]", view).forEach(b => b.onclick = async () => {
          const okConfirm = await confirmAsync(
            L("هل تريد إلغاء تسوية هذه الحركة البنكية؟ سيصبح السند المرتبط بها قابلًا للتعديل أو الحذف.",
              "Unreconcile this bank line? The voucher matched to it becomes editable again."),
            { title: L("إلغاء التسوية", "Unreconcile") });
          if (!okConfirm) return;
          try {
            await api(`/api/bank-transactions/${b.dataset.unrec}/unreconcile`, { method: "POST", body: {} });
            toast(L("تم إلغاء التسوية", "Reconciliation undone"));
            await render();
          } catch (e) { toast(e.message, "err"); }
        });
        $$("[data-rec]", view).forEach(b => b.onclick = async () => {
          const sug = await api("/api/bank-transactions/suggestions");
          const forTxn = sug.suggestions.find(s => s.transaction.id === Number(b.dataset.rec));
          openModal({
            title: t("banks.reconcile"), narrow: true,
            body: forTxn && forTxn.candidates.length
              ? `<p class="small muted">${ESC(t("banks.suggestions"))}</p>` + forTxn.candidates.map(c =>
                `<label class="menu-item" style="display:flex;gap:8px;align-items:center">
                    <input type="radio" name="cand" value="${c.id}"/>
                    <span>${ESC(c.payment_no)} · ${fmtDate(c.payment_date)} · ${fmtMoney(c.amount_cents)}</span></label>`).join("")
              : `<p class="muted small">${ESC(t("banks.noSuggestions"))}</p>`,
            footer: `<button class="btn" data-close>${ESC(t("common.cancel"))}</button>
                     ${forTxn && forTxn.candidates.length ? `<button class="btn btn-primary" id="rec-ok">${ESC(t("banks.confirm"))}</button>` : ""}`,
            onMount: (root, close) => {
              const btn = $("#rec-ok", root);
              if (!btn) return;
              btn.onclick = async () => {
                const sel = root.querySelector("input[name=cand]:checked");
                try {
                  await api(`/api/bank-transactions/${b.dataset.rec}/reconcile`, {
                    method: "POST", body: { payment_id: sel ? Number(sel.value) : null }
                  });
                  toast(t("common.saved")); close(); render();
                } catch (e) { toast(e.message, "err"); }
              };
            }
          });
        });
      }
    }
  };

  /* ==================================================================
     FIXED ASSETS
     ================================================================== */
  PAGES.assets = {
    perm: "assets.view",
    async render(view) {
      setTitle("nav.assets");
      const data = await api("/api/assets");
      const totalCost = data.assets.reduce((s, a) => s + a.cost_cents, 0);
      const totalAcc = data.assets.reduce((s, a) => s + a.accumulated_cents, 0);
      view.innerHTML = `${pageHead("assets.title", "assets.subtitle",
        `${can("assets.manage") ? `<button class="btn btn-primary btn-sm" id="new-asset">${icon("plus")} ${ESC(t("assets.new"))}</button>
          <button class="btn btn-sm" id="run-dep">${icon("refresh")} ${ESC(t("assets.runDepreciation"))}</button>` : ""}`)}
        <div class="grid cols-3">
          ${kpi({ label: t("assets.cost"), value: totalCost, icon: icon("box") })}
          ${kpi({ label: t("assets.accumulated"), value: totalAcc, icon: icon("arrowDown"), tone: "warn" })}
          ${kpi({ label: t("assets.bookValue"), value: totalCost - totalAcc, icon: icon("assets"), tone: "ok" })}
        </div>
        <div class="section-title"><span>${ESC(t("assets.title"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.name"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong>` },
          { key: "category", label: L("التصنيف", "Category") },
          { key: "start_date", label: L("بدء التشغيل", "In service"), render: r => fmtDate(r.start_date) },
          { key: "cost_cents", label: t("assets.cost"), cls: "num", render: r => fmtMoney(r.cost_cents) },
          { key: "useful_life_months", label: t("assets.life"), cls: "num" },
          { key: "bv", label: t("assets.bookValue"), cls: "num", render: r => fmtMoney(r.cost_cents - r.accumulated_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status === "active" ? "active" : "inactive") },
          { key: "act", cls: "act", label: t("common.actions"), render: r => can("assets.manage") ? `
              ${editButton(`/api/assets/${r.id}`, `الأصل «${r.name_ar}» (${r.code})`, `asset “${r.name_en}” (${r.code})`, [
                { name: "code", label: t("common.code"), required: true, value: r.code },
                { name: "name_ar", label: "الاسم بالعربية", required: true, value: r.name_ar },
                { name: "name_en", label: "Name (English)", required: true, value: r.name_en },
                { name: "category", label: L("التصنيف", "Category"), value: r.category || "" },
                { name: "start_date", label: L("بدء التشغيل", "In service"), type: "date", value: r.start_date || "" },
                { name: "cost", label: t("assets.cost"), type: "number", step: "0.01", required: true, value: (r.cost_cents || 0) / 100 },
                { name: "useful_life_months", label: t("assets.life"), type: "number", required: true, value: r.useful_life_months },
              ])}
              ${deleteButton(`/api/assets/${r.id}`, `الأصل «${r.name_ar}» (${r.code})`, `asset “${r.name_en}” (${r.code})`)}`
              : "" },
        ], data.assets))}
        <div class="section-title"><span>${ESC(t("assets.runs"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "run_no", label: L("الرمز التعريفي", "Reference ID") },
          { key: "period_start", label: t("common.from"), render: r => fmtDate(r.period_start) },
          { key: "period_end", label: t("common.to"), render: r => fmtDate(r.period_end) },
          { key: "total_cents", label: t("common.amount"), cls: "num", render: r => fmtMoney(r.total_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "journal_entry_id", label: L("القيد", "Journal"), render: r => r.journal_entry_id ? `<a href="#/journal/${r.journal_entry_id}">#${r.journal_entry_id}</a>` : "—" },
        ], data.runs, { compact: true }))}`;

      if (can("assets.manage")) {
        $("#new-asset").onclick = () => openForm({
          title: t("assets.new"),
          fields: [
            { name: "code", label: t("common.code"), required: true, value: "FA-" + Math.floor(100 + Math.random() * 900) },
            { name: "category", label: L("التصنيف", "Category") },
            { name: "name_ar", label: "الاسم بالعربية", required: true },
            { name: "name_en", label: "Name (English)", required: true },
            { name: "cost", label: t("assets.cost"), type: "number", step: "0.01", required: true },
            { name: "salvage", label: t("assets.salvage"), type: "number", step: "0.01", value: "0" },
            { name: "useful_life_months", label: t("assets.life"), type: "number", required: true, value: "60" },
            { name: "start_date", label: L("بدء التشغيل", "In service"), type: "date", value: today() },
            { name: "asset_account_code", label: t("common.account"), type: "select",
              options: [{ value: "1450", label: "1450 — أثاث ومفروشات" }, { value: "1430", label: "1430 — سيارات" },
                        { value: "1440", label: "1440 — حاسوب" }, { value: "1420", label: "1420 — مباني" }] },
          ],
          onSubmit: async (d) => { await api("/api/assets", { method: "POST", body: d }); toast(t("common.saved")); render(); }
        });
        $("#run-dep").onclick = () => openForm({
          title: t("assets.runDepreciation"),
          fields: [
            { name: "period_start", label: t("common.from"), type: "date", value: today().slice(0, 7) + "-01", required: true },
            { name: "period_end", label: t("common.to"), type: "date", value: today(), required: true },
          ],
          onSubmit: async (d) => {
            const res = await api("/api/assets/depreciation", { method: "POST", body: d });
            toast(`${t("toast.posted")} — ${fmtMoney(res.total_cents)}`); render();
          }
        });
      }
    }
  };

  /* ==================================================================
     PAYROLL
     ================================================================== */
  PAGES.payroll = {
    perm: "payroll.view",
    async render(view) {
      setTitle("nav.payroll");
      const data = await api("/api/payroll");
      const gross = data.employees.reduce((s, e) => s + e.basic_salary_cents + e.allowances_cents, 0);
      const net = data.employees.reduce((s, e) => s + e.basic_salary_cents + e.allowances_cents - e.deductions_cents - e.insurance_cents - e.tax_cents, 0);
      view.innerHTML = `${pageHead("payroll.title", "payroll.subtitle",
        `${can("payroll.manage") ? `<button class="btn btn-primary btn-sm" id="run-pay">${icon("refresh")} ${ESC(t("payroll.run"))}</button>
          <button class="btn btn-sm" id="new-emp">${icon("plus")} ${ESC(t("payroll.newEmployee"))}</button>` : ""}`)}
        <div class="grid cols-3">
          ${kpi({ label: t("payroll.gross"), value: gross, icon: icon("target") })}
          ${kpi({ label: t("payroll.net"), value: net, icon: icon("assets"), tone: "ok" })}
          ${kpi({ label: L("عدد الموظفين", "Employees"), value: data.employees.filter(e => e.is_active).length, icon: icon("users"), raw: true })}
        </div>
        <div class="section-title"><span>${ESC(t("payroll.employees"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.name"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong>` },
          { key: "department", label: L("الإدارة", "Department") },
          { key: "job_title", label: L("الوظيفة", "Job title") },
          { key: "basic_salary_cents", label: t("payroll.basic"), cls: "num", render: r => fmtMoney(r.basic_salary_cents) },
          { key: "allowances_cents", label: t("payroll.allowances"), cls: "num", render: r => fmtMoney(r.allowances_cents) },
          { key: "ded", label: t("payroll.deductions"), cls: "num", render: r => fmtMoney(r.deductions_cents + r.insurance_cents + r.tax_cents) },
          { key: "net", label: t("payroll.net"), cls: "num", render: r => fmtMoney(r.basic_salary_cents + r.allowances_cents - r.deductions_cents - r.insurance_cents - r.tax_cents) },
          { key: "act", cls: "act", label: t("common.actions"), render: r => can("payroll.manage") ? `
              ${editButton(`/api/employees/${r.id}`, `الموظف «${r.name_ar}» (${r.code})`, `employee “${r.name_en}” (${r.code})`, [
                { name: "code", label: t("common.code"), required: true, value: r.code },
                { name: "name_ar", label: "الاسم بالعربية", required: true, value: r.name_ar },
                { name: "name_en", label: "Name (English)", required: true, value: r.name_en },
                { name: "department", label: L("الإدارة", "Department"), value: r.department || "" },
                { name: "job_title", label: L("الوظيفة", "Job title"), value: r.job_title || "" },
                { name: "hire_date", label: L("تاريخ التعيين", "Hire date"), type: "date", value: r.hire_date || "" },
                { name: "basic_salary", label: t("payroll.basic"), type: "number", step: "0.01", required: true, value: (r.basic_salary_cents || 0) / 100 },
                { name: "allowances", label: t("payroll.allowances"), type: "number", step: "0.01", value: (r.allowances_cents || 0) / 100 },
                { name: "deductions", label: t("payroll.deductions"), type: "number", step: "0.01", value: (r.deductions_cents || 0) / 100 },
                { name: "is_active", label: t("common.status"), type: "select", value: r.is_active ? "1" : "0",
                  options: [{ value: "1", label: L("نشط", "Active") }, { value: "0", label: L("موقوف", "Inactive") }] },
              ])}
              ${deleteButton(`/api/employees/${r.id}`, `الموظف «${r.name_ar}» (${r.code})`, `employee “${r.name_en}” (${r.code})`)}`
              : "" },
        ], data.employees))}
        <div class="section-title"><span>${ESC(t("payroll.runs"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "run_no", label: L("الرمز التعريفي", "Reference ID") },
          { key: "period", label: t("payroll.period") },
          { key: "gross_cents", label: t("payroll.gross"), cls: "num", render: r => fmtMoney(r.gross_cents) },
          { key: "deductions_cents", label: t("payroll.deductions"), cls: "num", render: r => fmtMoney(r.deductions_cents) },
          { key: "net_cents", label: t("payroll.net"), cls: "num", render: r => fmtMoney(r.net_cents) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) },
          { key: "journal_entry_id", label: L("القيد", "Journal"), render: r => r.journal_entry_id ? `<a href="#/journal/${r.journal_entry_id}">#${r.journal_entry_id}</a>` : "—" },
        ], data.runs, { compact: true }))}`;

      if (can("payroll.manage")) {
        $("#run-pay").onclick = () => openForm({
          title: t("payroll.run"),
          fields: [{ name: "period", label: t("payroll.period"), value: today().slice(0, 7), required: true, hint: "YYYY-MM" }],
          onSubmit: async (d) => { const r = await api("/api/payroll/run", { method: "POST", body: d }); toast(`${t("toast.posted")} — ${fmtMoney(r.net_cents)}`); render(); }
        });
        $("#new-emp").onclick = () => openForm({
          title: t("payroll.newEmployee"),
          fields: [
            { name: "code", label: t("common.code"), required: true, value: "EMP-" + Math.floor(100 + Math.random() * 899) },
            { name: "name_ar", label: "الاسم بالعربية", required: true },
            { name: "name_en", label: "Name (English)", required: true },
            { name: "department", label: L("الإدارة", "Department") },
            { name: "job_title", label: L("الوظيفة", "Job title") },
            { name: "hire_date", label: L("تاريخ التعيين", "Hire date"), type: "date", value: today() },
            { name: "basic_salary", label: t("payroll.basic"), type: "number", step: "0.01", required: true },
            { name: "allowances", label: t("payroll.allowances"), type: "number", step: "0.01", value: "0" },
            { name: "deductions", label: t("payroll.deductions"), type: "number", step: "0.01", value: "0" },
          ],
          onSubmit: async (d) => { await api("/api/employees", { method: "POST", body: d }); toast(t("common.saved")); render(); }
        });
      }
    }
  };

  /* ==================================================================
     BUDGETS
     ================================================================== */
  PAGES.budgets = {
    perm: "budgets.view",
    async render(view) {
      setTitle("nav.budgets");
      const data = await api("/api/budgets");
      const rows = data.variance;
      const over = rows.filter(r => r.variance_cents < 0);
      view.innerHTML = `${pageHead("budgets.title", "budgets.subtitle",
        `${can("budgets.manage") ? `<button class="btn btn-primary btn-sm" id="new-budget">${icon("plus")} ${ESC(t("budgets.new"))}</button>` : ""}
         <a class="btn btn-sm" href="#/reports?tab=vendor-payables">${icon("reports")} ${ESC(t("nav.reports"))}</a>`)}
        <div class="grid cols-3">
          ${kpi({ label: t("budgets.budget"), value: rows.reduce((s, r) => s + r.budget_cents, 0), icon: icon("pie") })}
          ${kpi({ label: t("budgets.actual"), value: rows.reduce((s, r) => s + r.actual_cents, 0), icon: icon("grid") })}
          ${kpi({ label: L("بنود تجاوزت الموازنة", "Over-budget lines"), value: over.length, icon: icon("alert"), tone: "danger", raw: true })}
        </div>
        <div class="section-title"><span>${ESC(L("الموازنة مقابل الفعلي", "Budget vs actual"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "code", label: t("common.code") },
          { key: "name_ar", label: t("common.name"), render: r => ESC(APP.lang === "ar" ? r.name_ar : r.name_en) },
          { key: "budget_cents", label: t("budgets.budget"), cls: "num", render: r => fmtMoney(r.budget_cents) },
          { key: "actual_cents", label: t("budgets.actual"), cls: "num", render: r => fmtMoney(r.actual_cents) },
          { key: "variance_cents", label: t("budgets.variance"), cls: "num", render: r => `<span class="${r.variance_cents < 0 ? "neg" : "pos"}">${fmtMoney(r.variance_cents)}</span>` },
          { key: "util", label: L("الاستخدام", "Utilization"), render: r => {
              const p = r.budget_cents ? Math.min(150, r.actual_cents / r.budget_cents * 100) : 0;
              return `<div class="progress ${p > 100 ? "danger" : p > 85 ? "warn" : ""}"><span style="width:${Math.min(100, p)}%"></span></div>
                      <small class="muted">${p.toFixed(0)}%</small>`;
            } },
          { key: "act", cls: "act", label: t("common.actions"), render: r => {
              const lines = (data.budgets || []).filter(b => b.account_id === r.account_id || b.code === r.code);
              if (!can("budgets.manage") || !lines.length) return "—";
              return lines.map(b => `
                ${editButton(`/api/budgets/${b.id}`, `بند موازنة «${b.name_ar}» — شهر ${b.period_month}`, `budget line “${b.name_en || b.name_ar}” — month ${b.period_month}`, [
                  { name: "budget", label: t("budgets.budget"), type: "number", step: "0.01", required: true, value: (b.budget_cents || 0) / 100 },
                  { name: "period_month", label: L("الشهر", "Month"), type: "number", value: b.period_month },
                  { name: "notes", label: t("common.description"), value: b.notes || "" },
                ])}
                ${deleteButton(`/api/budgets/${b.id}`, `بند موازنة «${b.name_ar}» — شهر ${b.period_month}`, `budget line “${b.name_en || b.name_ar}” — month ${b.period_month}`)}`).join("");
            } },
        ], rows, {
          footer: `<tr><td colspan="2">${ESC(t("common.total"))}</td>
            <td class="num">${fmtMoney(rows.reduce((s, r) => s + r.budget_cents, 0))}</td>
            <td class="num">${fmtMoney(rows.reduce((s, r) => s + r.actual_cents, 0))}</td>
            <td class="num">${fmtMoney(rows.reduce((s, r) => s + r.variance_cents, 0))}</td><td></td><td></td></tr>`
        }))}`;
      if (can("budgets.manage")) $("#new-budget").onclick = async () => {
        const [accs, meta] = await Promise.all([api("/api/accounts"), api("/api/meta")]);
        openForm({
          title: t("budgets.new"),
          fields: [
            { name: "fiscal_year_id", label: t("periods.year"), type: "select", required: true,
              options: meta.fiscal_years.map(f => ({ value: f.id, label: f.name })) },
            { name: "account_id", label: t("common.account"), type: "select", required: true,
              options: accs.accounts.filter(a => a.is_postable && ["expense", "revenue"].includes(a.type))
                .map(a => ({ value: a.id, label: `${a.code} — ${a.name_ar}` })) },
            { name: "cost_center_id", label: t("common.costCenter"), type: "select",
              options: [{ value: "", label: "—" }, ...meta.cost_centers.map(c => ({ value: c.id, label: c.name_ar }))] },
            { name: "period_month", label: t("budgets.month"), type: "select", required: true,
              options: Array.from({ length: 12 }, (_, i) => ({ value: i + 1, label: String(i + 1) })) },
            { name: "budget", label: t("budgets.budget"), type: "number", step: "0.01", required: true },
          ],
          onSubmit: async (d) => { await api("/api/budgets", { method: "POST", body: d }); toast(t("common.saved")); render(); }
        });
      };
    }
  };

  /* ==================================================================
     REPORTS
     ================================================================== */
  PAGES.reports = {
    perm: "reports.view",
    async render(view) {
      setTitle("nav.reports");
      const tab = new URLSearchParams((location.hash.split("?")[1] || "")).get("tab") || "trial-balance";
      const from = yearStart(), to = today();
      const tabs = [
        ["trial-balance", "reports.trialBalance"],
        ["income-statement", "reports.incomeStatement"],
        ["balance-sheet", "reports.balanceSheet"],
        ["cash-flow", "reports.cashFlow"],
        ["aging-ar", "reports.agingAR"],
        ["aging-ap", "reports.agingAP"],
        ["vendor-payables", "reports.vendorPayables"],
      ];
      view.innerHTML = `${pageHead("reports.title", "reports.subtitle", printBtn())}
        <div class="tabs">${tabs.map(([k, l]) => `<button class="tab ${tab === k ? "active" : ""}" data-tab="${k}">${ESC(t(l))}</button>`).join("")}</div>
        <div class="filters">
          <div class="field"><span>${ESC(t("common.from"))}</span><input type="date" id="r-from" value="${from}"/></div>
          <div class="field"><span>${ESC(t("common.to"))}</span><input type="date" id="r-to" value="${to}"/></div>
          <button class="btn btn-sm btn-primary" id="r-apply">${ESC(t("common.filter"))}</button>
          <div style="flex:1"></div>
          <span id="r-export"></span>
        </div>
        <div id="report-body"><div class="skeleton"></div></div>`;
      $$("[data-tab]", view).forEach(b => b.onclick = () => { location.hash = `#/reports?tab=${b.dataset.tab}`; });

      const body = $("#report-body");
      const load = async () => {
        const f = $("#r-from").value, t2 = $("#r-to").value;
        body.innerHTML = `<div class="skeleton"></div>`;
        if (tab === "trial-balance") {
          $("#r-export").innerHTML = reportExports("trial-balance", { from: f, to: t2 });
          const d = await api(`/api/reports/trial-balance?from=${f}&to=${t2}`);
          body.innerHTML = `${card(`${t("reports.trialBalance")} — ${f} → ${t2}`, renderTable([
            { key: "code", label: t("common.code") },
            { key: "name_ar", label: t("common.account"), render: r => ESC(APP.lang === "ar" ? r.name_ar : r.name_en) },
            { key: "opening", label: t("reports.opening"), cls: "num", render: r => fmtMoney(r.opening) },
            { key: "period_debit", label: t("reports.periodDebit"), cls: "num", render: r => fmtMoney(r.period_debit) },
            { key: "period_credit", label: t("reports.periodCredit"), cls: "num", render: r => fmtMoney(r.period_credit) },
            { key: "closing", label: t("reports.closing"), cls: "num", render: r => fmtMoney(r.closing) },
          ], d.rows, {
            footer: `<tr><td colspan="2">${ESC(t("common.total"))}</td>
              <td class="num">${fmtMoney(d.totals.opening_net)}</td>
              <td class="num">${fmtMoney(d.totals.period_debit)}</td>
              <td class="num">${fmtMoney(d.totals.period_credit)}</td>
              <td class="num">${fmtMoney(d.totals.closing_net)}</td></tr>`
          }), `<span class="badge ${d.balanced ? "green" : "red"}">${ESC(d.balanced ? t("reports.balanced") : t("reports.notBalanced"))}</span>`) }`;
        } else if (tab === "income-statement") {
          $("#r-export").innerHTML = reportExports("income-statement", { from: f, to: t2 });
          const d = await api(`/api/reports/income-statement?from=${f}&to=${t2}`);
          const line = (i) => `<div class="stat-line"><span>${ESC(i.code)} — ${ESC(APP.lang === "ar" ? i.name_ar : i.name_en)}</span><strong>${fmtMoney(i.amount_cents)}</strong></div>`;
          body.innerHTML = `<div class="grid cols-2">
            ${card(t("reports.revenue"), d.revenue.map(line).join("") + `<div class="stat-line" style="border-top:2px solid var(--border-strong)"><strong>${ESC(t("common.total"))}</strong><strong>${fmtMoney(d.total_revenue_cents)}</strong></div>`)}
            ${card(t("reports.expenses"), d.expenses.map(line).join("") + `<div class="stat-line" style="border-top:2px solid var(--border-strong)"><strong>${ESC(t("common.total"))}</strong><strong>${fmtMoney(d.total_expenses_cents)}</strong></div>`)}
          </div>
          <div class="grid cols-3" style="margin-top:16px">
            ${kpi({ label: t("reports.grossProfit"), value: d.gross_profit_cents, icon: icon("assets") })}
            ${kpi({ label: t("reports.profit"), value: d.net_profit_cents, icon: icon("arrowUp"), tone: d.net_profit_cents >= 0 ? "ok" : "danger" })}
            ${kpi({ label: L("هامش صافي الربح", "Net margin"), value: d.total_revenue_cents ? ((d.net_profit_cents / d.total_revenue_cents) * 100).toFixed(1) + "%" : "—", icon: icon("percent"), raw: true })}
          </div>`;
        } else if (tab === "balance-sheet") {
          $("#r-export").innerHTML = reportExports("balance-sheet", { as_of: t2 });
          const d = await api(`/api/reports/balance-sheet?as_of=${t2}`);
          const line = (i) => `<div class="stat-line"><span>${ESC(i.code)} — ${ESC(APP.lang === "ar" ? i.name_ar : i.name_en)}</span><strong>${fmtMoney(i.amount_cents)}</strong></div>`;
          body.innerHTML = `<div class="grid cols-3">
            ${card(t("reports.assets"), d.assets.map(line).join("") + `<div class="stat-line"><strong>${ESC(t("common.total"))}</strong><strong>${fmtMoney(d.total_assets_cents)}</strong></div>`)}
            ${card(t("reports.liabilities"), d.liabilities.map(line).join("") + `<div class="stat-line"><strong>${ESC(t("common.total"))}</strong><strong>${fmtMoney(d.total_liabilities_cents)}</strong></div>`)}
            ${card(t("reports.equity"), d.equity.map(line).join("") + `<div class="stat-line"><strong>${ESC(t("common.total"))}</strong><strong>${fmtMoney(d.total_equity_cents)}</strong></div>`)}
          </div>
          <p class="field-hint" style="margin-top:10px">${ESC(L("تحقق التوازن: الأصول = الالتزامات + حقوق الملكية", "Balance check: assets = liabilities + equity"))}:
            <span class="badge ${d.balanced ? "green" : "red"}">${d.balanced ? icon("check","ico-sm") : icon("close","ico-sm")}</span></p>`;
        } else if (tab === "cash-flow") {
          $("#r-export").innerHTML = "";
          const d = await api(`/api/reports/cash-flow?from=${f}&to=${t2}`);
          body.innerHTML = `<div class="grid cols-3">
            ${kpi({ label: t("reports.inflow"), value: d.inflow_cents, icon: icon("arrowUp"), tone: "ok" })}
            ${kpi({ label: t("reports.outflow"), value: d.outflow_cents, icon: icon("arrowDown"), tone: "danger" })}
            ${kpi({ label: t("reports.netCash"), value: d.net_cents, icon: icon("assets") })}
          </div>
          ${card(t("reports.cashFlow"), renderTable([
            { key: "entry_date", label: t("common.date") },
            { key: "entry_no", label: L("الرمز التعريفي", "Reference ID") },
            { key: "description", label: t("common.description") },
            { key: "d", label: t("common.debit"), cls: "num", render: r => fmtMoney(r.d) },
            { key: "c", label: t("common.credit"), cls: "num", render: r => fmtMoney(r.c) },
          ], d.rows, { compact: true }), `<span class="muted small">${ESC(L("الطريقة غير المباشرة — من حركات حسابات البنوك والصناديق", "Indirect view — from cash & bank GL movements"))}</span>`)}`;
        } else if (tab === "aging-ar" || tab === "aging-ap") {
          const kind = tab === "aging-ar" ? "sales" : "purchase";
          $("#r-export").innerHTML = reportExports("aging", { as_of: t2, type: kind });
          const d = await api(`/api/reports/aging?as_of=${t2}&kind=${kind}`);
          body.innerHTML = `<div class="grid cols-5" style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px">
              ${Object.keys(d.buckets).map(b => `<div class="card kpi ${b === "over_90" ? "danger" : b === "current" ? "ok" : ""}">
                  <div><div class="kpi-label">${ESC(t("reports.bucket." + b))}</div>
                  <div class="kpi-value" style="font-size:17px">${fmtMoney(d.buckets[b])}</div></div></div>`).join("")}
            </div>
            ${card(t("payables.invoices"), renderTable([
              { key: "invoice_no", label: L("الرمز التعريفي", "Reference ID") },
              { key: "party_ar", label: kind === "sales" ? t("common.customer") : t("common.vendor") },
              { key: "invoice_date", label: t("common.date") },
              { key: "due_date", label: t("common.dueDate") },
              { key: "total_cents", label: t("common.total"), cls: "num", render: r => fmtMoney(r.total_cents) },
              { key: "remaining_cents", label: t("common.remaining"), cls: "num", render: r => fmtMoney(r.remaining_cents) },
              { key: "days_overdue", label: t("reports.daysOverdue"), cls: "num", render: r => r.days_overdue > 0 ? `<span class="neg">${r.days_overdue}</span>` : "—" },
              { key: "bucket", label: t("payables.aging"), render: r => `<span class="badge ${r.bucket === "current" ? "green" : r.bucket === "over_90" ? "red" : "amber"}">${ESC(t("reports.bucket." + r.bucket))}</span>` },
            ], d.items))}`;
        } else if (tab === "vendor-payables") {
          $("#r-export").innerHTML = reportExports("vendor-payables", { as_of: t2 });
          const d = await api(`/api/reports/vendor-payables?as_of=${t2}`);
          body.innerHTML = `<div class="grid cols-4">
              ${kpi({ label: t("payables.outstanding"), value: d.totals.outstanding_cents, icon: icon("reports"), tone: "warn" })}
              ${kpi({ label: t("payables.overdue"), value: d.totals.overdue_cents, icon: icon("alert"), tone: "danger" })}
              ${kpi({ label: t("payables.invoiced"), value: d.totals.invoiced_cents, icon: icon("grid") })}
              ${kpi({ label: t("payables.journal"), value: d.totals.journal_balance_cents, icon: icon("transfer") })}
            </div>
            ${card(t("reports.vendorPayables"), renderTable([
              { key: "code", label: t("common.code") },
              { key: "name_ar", label: t("common.vendor"), render: r => ESC(APP.lang === "ar" ? r.name_ar : r.name_en) },
              { key: "invoiced_cents", label: t("payables.invoiced"), cls: "num", render: r => fmtMoney(r.invoiced_cents) },
              { key: "paid_cents", label: t("payables.paid"), cls: "num", render: r => fmtMoney(r.paid_cents) },
              { key: "outstanding_cents", label: t("payables.outstanding"), cls: "num", render: r => fmtMoney(r.outstanding_cents) },
              { key: "overdue_cents", label: t("payables.overdue"), cls: "num", render: r => r.overdue_cents ? `<span class="neg">${fmtMoney(r.overdue_cents)}</span>` : "—" },
              { key: "next_due_date", label: t("payables.nextDue"), render: r => r.next_due_date ? fmtDate(r.next_due_date) : "—" },
              { key: "journal_balance_cents", label: t("payables.journal"), cls: "num", render: r => fmtMoney(r.journal_balance_cents) },
            ], d.vendors))}`;
        }
      };
      $("#r-apply").onclick = load;
      $("#r-to").onchange = load;
      await load();
      wireExports(view);
    }
  };

  /* ==================================================================
     USERS
     ================================================================== */
  PAGES.users = {
    perm: "users.manage",
    async render(view) {
      setTitle("nav.users");
      const data = await api("/api/users");
      view.innerHTML = `${pageHead("users.title", "users.subtitle",
        can("users.manage") ? `<button class="btn btn-primary btn-sm" id="new-user">${icon("plus")} ${ESC(t("users.new"))}</button>` : "")}
        ${card("", renderTable([
          { key: "username", label: t("users.username"), render: r => `<strong>${ESC(r.username)}</strong>` },
          { key: "full_name_ar", label: t("common.name"), render: r => `${ESC(APP.lang === "ar" ? r.full_name_ar : r.full_name_en)}<br><small class="muted">${ESC(r.email || "")}</small>` },
          { key: "role_code", label: t("users.role"), render: r => `<span class="badge violet">${ESC(APP.lang === "ar" ? r.role_ar : r.role_en)}</span>` },
          { key: "last_login_at", label: t("users.lastLogin"), render: r => r.last_login_at ? `<span dir="ltr">${ESC(r.last_login_at)}</span>` : "—" },
          { key: "is_active", label: t("common.status"), render: r => statusBadge(r.is_active ? "active" : "inactive") },
          { key: "act", cls: "act", label: t("common.actions"), render: r => `<button class="btn btn-sm btn-ghost" data-perm="${r.role_code}">${ESC(t("users.permissions"))}</button>` },
        ], data.users))}
        <div class="section-title"><span>${ESC(t("users.role"))}</span><span class="line"></span></div>
        ${card("", renderTable([
          { key: "name_ar", label: t("users.role"), render: r => `<strong>${ESC(APP.lang === "ar" ? r.name_ar : r.name_en)}</strong>` },
          { key: "code", label: t("common.code") },
          { key: "data_scope", label: L("نطاق البيانات", "Data scope") },
          { key: "count", label: L("عدد الصلاحيات", "Permissions"), cls: "num", render: r => {
              const p = JSON.parse(r.permissions || "[]"); return p.includes("*") ? "all" : p.length; } },
        ], data.roles))}`;
      $$("[data-perm]", view).forEach(b => b.onclick = () => {
        const role = data.roles.find(r => r.code === b.dataset.perm);
        const perms = JSON.parse(role.permissions || "[]");
        openModal({
          title: `${ESC(APP.lang === "ar" ? role.name_ar : role.name_en)} — ${ESC(t("users.permissions"))}`,
          body: `<div class="chip-row">${(perms.includes("*") ? ["*"] : perms).map(p => `<span class="chip active">${ESC(p)}</span>`).join("")}</div>
                 <p class="field-hint" style="margin-top:12px">${ESC(L("الصلاحيات تُفرض على الخادم في كل طلب، وليست إخفاءً في الواجهة فقط.",
                   "Permissions are enforced server-side on every request — not just hidden in the UI."))}</p>`
        });
      });
      const nu = $("#new-user");
      if (nu) nu.onclick = () => openForm({
        title: t("users.new"),
        fields: [
          { name: "username", label: t("users.username"), required: true },
          { name: "email", label: t("users.email"), type: "email" },
          { name: "full_name_ar", label: t("users.fullNameAr"), required: true },
          { name: "full_name_en", label: t("users.fullNameEn"), required: true },
          { name: "role_code", label: t("users.role"), type: "select", required: true,
            options: data.roles.filter(r => r.code !== "super_admin").map(r => ({ value: r.code, label: APP.lang === "ar" ? r.name_ar : r.name_en })) },
          { name: "password", label: t("users.password"), type: "password", required: true, hint: "≥ 10" },
        ],
        onSubmit: async (d) => { await api("/api/users", { method: "POST", body: d }); toast(t("common.saved")); render(); }
      });
    }
  };

  /* ==================================================================
     FISCAL PERIODS
     ================================================================== */
  PAGES.periods = {
    perm: "reports.view",
    async render(view) {
      setTitle("nav.periods");
      const data = await api("/api/fiscal-periods");
      const canClose = can("period.close");
      // Show the period containing today first, then most recent periods.
      const today = new Date().toISOString().slice(0, 10);
      const current = data.periods.find(p => p.start_date <= today && today <= p.end_date);
      data.periods.sort((a, b) => {
        const aw = current && a.fiscal_year_id === current.fiscal_year_id ? 0 : 1;
        const bw = current && b.fiscal_year_id === current.fiscal_year_id ? 0 : 1;
        return aw - bw || b.start_date.localeCompare(a.start_date);
      });
      view.innerHTML = `${pageHead("periods.title", "periods.subtitle")}
        ${card("", renderTable([
          { key: "fiscal_year", label: t("periods.year") },
          { key: "period_no", label: t("periods.period"), render: r => String(r.period_no).padStart(2, "0") },
          { key: "start_date", label: t("common.from"), render: r => fmtDate(r.start_date) },
          { key: "end_date", label: t("common.to"), render: r => fmtDate(r.end_date) },
          { key: "status", label: t("common.status"), render: r => statusBadge(r.status) +
              (current && r.id === current.id ? ` <span class="pill pill-accent">${ESC(L("الحالية", "Current"))}</span>` : "") },
          { key: "closed_by_name", label: L("أقفلها", "Closed by"), render: r => ESC(r.closed_by_name || "—") },
          { key: "closed_at", label: L("تاريخ الإقفال", "Closed at"), render: r => ESC(r.closed_at || "—") },
          { key: "act", cls: "act", label: "", render: r => canClose
              ? (r.status === "open"
                ? `<button class="btn btn-sm btn-ghost" data-close-p="${r.id}">${ESC(t("periods.close"))}</button>`
                : `<button class="btn btn-sm btn-ghost" data-open-p="${r.id}">${ESC(t("periods.reopen"))}</button>`)
              : "" },
        ], data.periods))}`;
      $$("[data-close-p]", view).forEach(b => b.onclick = () => confirmDialog(
        t("periods.close") + "؟", async () => {
          try { await api(`/api/fiscal-periods/${b.dataset.closeP}/close`, { method: "POST", body: {} }); toast(t("common.saved")); render(); }
          catch (e) { toast(e.message, "err"); }
        }, { title: t("periods.close") }));
      $$("[data-open-p]", view).forEach(b => b.onclick = async () => {
        try { await api(`/api/fiscal-periods/${b.dataset.openP}/reopen`, { method: "POST", body: {} }); toast(t("common.saved")); render(); }
        catch (e) { toast(e.message, "err"); }
      });
    }
  };

  /* ==================================================================
     AUDIT LOG
     ================================================================== */
  PAGES.audit = {
    perm: "audit.view",
    async render(view) {
      setTitle("nav.audit");
      const data = await api("/api/audit-logs?limit=300");
      view.innerHTML = `${pageHead("audit.title", "audit.subtitle")}
        ${card("", renderTable([
          { key: "created_at", label: t("audit.time"), render: r => `<span class="mono small" dir="ltr">${ESC(r.created_at)}</span>` },
          { key: "username", label: t("audit.user") },
          { key: "action", label: t("audit.action"), render: r => `<span class="pill">${ESC(r.action)}</span>` },
          { key: "entity", label: t("audit.entity"), render: r => `<span class="mono small" dir="ltr">${ESC(r.entity || "—")} #${r.entity_id ?? "—"}</span>` },
          { key: "details", label: t("audit.details"), render: r => `<span class="json-cell" dir="ltr" title="${ESC(r.details || "")}">${ESC((r.details || "").replace(/\s+/g, " ").slice(0, 90) || "—")}</span>` },
          { key: "ip", label: "IP", render: r => ESC(r.ip || "—") },
        ], data.logs, { compact: true }))}`;
    }
  };

  /* ==================================================================
     AI ASSISTANT
     ================================================================== */
  PAGES.assistant = {
    perm: "ai.use",
    async render(view) {
      setTitle("nav.assistant");
      const suggestions = APP.lang === "ar"
        ? ["ما إجمالي مديونية الموردين؟", "ما صافي الربح هذا العام؟", "ما هي السيولة المتاحة؟", "أكبر بنود المصروفات؟", "أعمار ديون العملاء؟", "ملخص الأداء"]
        : ["What are total vendor payables?", "What is the net profit this year?", "What is the available liquidity?", "Largest expense lines?", "Receivables aging?", "Performance summary"];
      view.innerHTML = `${pageHead("assistant.title", "assistant.subtitle", `<span class="badge violet">${icon("sparkle")} ${ESC(L("تحليلات من القيود المرحّلة", "Analytics from posted entries"))}</span>`)}
        <div class="split">
          ${card("", `<div class="assistant" id="chat">
            <div class="msg bot">${ESC(L("مرحبًا. اسألني عن الأرباح، المصروفات، السيولة، أو مديونية الموردين. كل إجابة مبنية على بياناتك الفعلية وضمن صلاحياتك.",
              "Hello. Ask me about profit, expenses, liquidity or vendor payables. Every answer is computed from your actual data within your permissions."))}</div>
            <div class="filters" style="margin:0">
              <input id="chat-input" placeholder="${ESC(t("assistant.placeholder"))}" style="flex:1"/>
              <button class="btn btn-primary" id="chat-send">${ESC(t("assistant.send"))}</button>
            </div>
            <p class="field-hint">${ESC(t("assistant.disclaimer"))}</p>
          </div>`)}
          <div>
            ${card(t("assistant.suggestions"), `<div class="chip-row" style="flex-direction:column;align-items:stretch">
              ${suggestions.map(s => `<button class="chip" data-q="${ESC(s)}" style="text-align:start">${ESC(s)}</button>`).join("")}
            </div>`)}
          </div>
        </div>`;
      const chat = $("#chat");
      const input = $("#chat-input");
      const send = async (text) => {
        const q = (text || input.value || "").trim();
        if (!q) return;
        chat.insertAdjacentHTML("beforeend", `<div class="msg user">${ESC(q)}</div>`);
        input.value = "";
        const loadingId = "ld" + Date.now();
        chat.insertAdjacentHTML("beforeend", `<div class="msg bot" id="${loadingId}">${ESC(t("common.loading"))}</div>`);
        try {
          const res = await api("/api/assistant/ask", { method: "POST", body: { question: q } });
          document.getElementById(loadingId).outerHTML =
            `<div class="msg bot">${ESC(res.answer)}<span class="src">${ESC(t("assistant.source"))}: ${ESC(res.source)}</span></div>`;
        } catch (e) {
          document.getElementById(loadingId).outerHTML = `<div class="msg bot">${icon("alert")} ${ESC(e.message)}</div>`;
        }
        chat.scrollIntoView({ block: "end" });
      };
      $("#chat-send").onclick = () => send();
      input.onkeydown = (e) => { if (e.key === "Enter") send(); };
      $$("[data-q]", view).forEach(b => b.onclick = () => send(b.dataset.q));
    }
  };
})();
