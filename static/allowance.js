/* ============================================================
   ELTARANISI ERP — إعاشة العاملين (staff subsistence allowance)
   The sheet mirrors the paper/Excel form: one row per worker, the
   day grid (1..31) with letter codes, derived day counts and the
   money total per period. All totals are recomputed on the server.
   ============================================================ */
(function () {
  const L = (ar, en) => (APP.lang === "ar" ? ar : en);
  const CODES = [
    { code: "", ar: "حضور", en: "Present", cls: "" },
    { code: "ام", ar: "مرضي", en: "Sick", cls: "c-sick" },
    { code: "بم", ar: "بدون مرتب", en: "Unpaid", cls: "c-unpaid" },
    { code: "غ", ar: "غياب", en: "Absent", cls: "c-abs" },
    { code: "م", ar: "مامورية", en: "Mission", cls: "c-mis" },
    { code: "خ", ar: "خصم إعاشة", en: "Deduction", cls: "c-ded" },
  ];
  const CODE_CLS = Object.fromEntries(CODES.map(c => [c.code, c.cls]));
  const CODE_LABEL = Object.fromEntries(CODES.map(c => [c.code, APP.lang === "ar" ? c.ar : c.en]));

  let S = { meta: null, period: null, grid: true, dirty: false };

  /** page title + breadcrumbs (setPageTitle lives in app.js) */
  function setTitle(title, crumbs) {
    if (typeof setPageTitle === "function") setPageTitle(title, crumbs || []);
  }

  /* ---------------------------------------------------------- helpers */
  function dmy(iso) { return iso ? isoToDmy(String(iso).slice(0, 10)) : "—"; }
  function daysBetween(a, b) {
    return Math.round((new Date(b) - new Date(a)) / 86400000) + 1;
  }
  function monthRange(monthValue) {
    const [y, m] = monthValue.split("-").map(Number);
    const last = new Date(y, m, 0).getDate();
    return { start: `${monthValue}-01`, end: `${monthValue}-${String(last).padStart(2, "0")}` };
  }
  function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
  function rateOf(line) { return line.daily_rate_cents / 100; }

  /** day number (1-based) of an ISO date inside the sheet period */
  function dayIndex(iso, periodStart) {
    return Math.round((new Date(iso + "T00:00:00") - new Date(periodStart + "T00:00:00")) / 86400000) + 1;
  }
  /** the paid window of one line: [firstDay, lastDay] inside the period */
  function windowOf(line, period) {
    const first = line.att_from ? Math.max(1, dayIndex(String(line.att_from).slice(0, 10), period.start_date)) : 1;
    const last = line.att_to
      ? Math.min(period.days, dayIndex(String(line.att_to).slice(0, 10), period.start_date))
      : period.days;
    return [Math.min(first, last), Math.max(first, last)];
  }

  /** client-side preview of the server maths (server always wins on save) */
  function computePreview(line, days, period) {
    const p = period || S.period || { days, start_date: line.att_from || "" };
    const [first, last] = windowOf(line, p);
    const winDays = Math.max(0, last - first + 1);
    const codes = {};
    Object.entries(line.day_codes || {}).forEach(([k, v]) => {
      const d = Number(k);
      if (d >= first && d <= last) codes[k] = v;
    });
    const derived = { ام: "leave_sick", بم: "leave_unpaid", غ: "absence_days", م: "mission_days", خ: "ded_allowance_days" };
    const counts = { leave_sick: 0, leave_unpaid: 0, absence_days: 0, mission_days: 0, ded_allowance_days: 0 };
    // the grid is authoritative even when it is empty — clearing every cell must
    // also clear the derived counters (otherwise «خ» stuck around after undo)
    Object.values(codes).forEach(c => { if (derived[c]) counts[derived[c]] += 1; });
    Object.assign(line, counts);
    const away = num(line.leave_annual) + num(line.leave_sick) + num(line.leave_unpaid)
      + num(line.absence_days) + num(line.mission_days);
    const present = Math.max(0, winDays - away);
    const net = Math.max(0, present - num(line.ded_allowance_days) - num(line.ded_salary_days));
    line.present_days = present;
    line.net_days = net;
    line.days_in_period = winDays;
    line.total_cents = Math.round(net * num(rateOf(line)) * 100 + num(line.adjustments) * 100);
    return line;
  }
  function totals() {
    const lines = (S.period && S.period.lines) || [];
    return {
      people: lines.length,
      days: lines.reduce((s, l) => s + num(l.net_days), 0),
      cents: lines.reduce((s, l) => s + num(l.total_cents), 0),
    };
  }
  function markDirty() {
    S.dirty = true;
    const b = $("#al-save");
    if (b) b.classList.add("btn-primary");
  }
  /** Recompute each section's subtotal row from the live grid. */
  function recalcSections() {
    const lines = (S.period && S.period.lines) || [];
    document.querySelectorAll("tr.sheet-section-total").forEach(tr => {
      const dept = tr.getAttribute("data-section") || "";
      const group = lines.filter(l => (l.department || "") === dept);
      const days = group.reduce((s, l) => s + num(l.net_days), 0);
      const cents = group.reduce((s, l) => s + num(l.total_cents), 0);
      const d = tr.querySelector(".sec-days");
      const m = tr.querySelector(".sec-money");
      if (d) d.textContent = days;
      if (m) m.textContent = moneyLabel(cents);
    });
  }
  function recalcFooter() {
    const tot = totals();
    recalcSections();
    const el = $("#al-total");
    if (el) el.textContent = moneyLabel(tot.cents);
    const d = $("#al-days");
    if (d) d.textContent = tot.days;
    const p = $("#al-people");
    if (p) p.textContent = tot.people;
  }

  /* ---------------------------------------------------------- list page */
  async function renderList(view) {
    setTitle(t("allow.nav"), [L("الرواتب والإعاشة", "Payroll & subsistence")]);
    S.meta = S.meta || await api("/api/allowance/meta");
    const periods = await api("/api/allowance/periods");
    const rows = periods.periods || [];
    const canManage = can("allowance.manage");
    const emptyRoster = !(S.meta.people || []).length;

    view.innerHTML = `
      ${pageHeadElement()}
      <div class="grid cols-3">
        ${kpi({ label: L("كشوف الإعاشة", "Subsistence sheets"), value: rows.length, raw: true, icon: icon("payroll") })}
        ${kpi({ label: L("عمال الكشف", "Workers on the roster"), value: (S.meta.people || []).length, raw: true, icon: icon("users") })}
        ${kpi({ label: L("الأقسام", "Departments"), value: (S.meta.departments || []).length, raw: true, icon: icon("reports") })}
      </div>
      ${emptyRoster ? `<div class="card"><div class="card-body">
        <p style="margin:0 0 10px">${ESC(L("لا يوجد كشف عمال بعد. يمكنك استيراد الكشف من ملف الإكسيل الخاص بالشركة ثم تعديل الأسماء والفئات.",
          "No roster yet. Import the roster from the company's Excel file, then edit names and daily rates."))}</p>
        ${canManage ? `<button class="btn btn-primary" id="al-import">${icon("download")} ${ESC(L("استيراد كشف العمال", "Import roster"))}</button>` : ""}
      </div></div>` : ""}
      ${card(L("كشوف الإعاشة", "Subsistence sheets"),
        renderTable([
          { key: "id", label: L("الرمز", "Ref"), render: r => `<span class="mono">AL-${String(r.id).padStart(4, "0")}</span>` },
          { key: "department", label: L("القسم", "Department"), cls: "wrap" },
          { key: "start_date", label: L("من", "From"), render: r => fmtDate(r.start_date) },
          { key: "end_date", label: L("إلى", "To"), render: r => fmtDate(r.end_date) },
          { key: "people_count", label: L("عدد العمال", "Workers"), cls: "num" },
          { key: "total_days", label: L("أيام الإعاشة", "Days"), cls: "num" },
          { key: "total_cents", label: L("الإجمالي", "Total"), cls: "num", render: r => `<strong>${fmtMoney(r.total_cents)}</strong>` },
          { key: "status", label: L("الحالة", "Status"), render: r => statusBadge(r.status === "posted" ? "posted" : "draft") },
          { key: "act", label: L("إجراءات", "Actions"), cls: "act", render: r => `
              <a class="btn btn-sm" href="#/allowance/${r.id}">${icon("eye")} ${ESC(L("فتح", "Open"))}</a>` },
        ], rows, { empty: L("لا توجد كشوف بعد — اضغط «كشف جديد»", "No sheets yet — press “New sheet”") }))}
    `;
    wireList(view);
  }

  function pageHeadElement() {
    const canManage = can("allowance.manage");
    return `<div class="page-head">
      <div><h1>${ESC(t("allow.nav"))}</h1><p>${ESC(t("allow.subtitle"))}</p></div>
      <div class="actions">
        ${canManage ? `<button class="btn btn-sm" id="al-roster">${icon("users")} ${ESC(L("كشف العمال", "Roster"))}</button>
        <button class="btn btn-primary" id="al-new">${icon("plus")} ${ESC(L("كشف جديد", "New sheet"))}</button>` : ""}
      </div></div>`;
  }

  function wireList(view) {
    const imp = $("#al-import", view);
    if (imp) imp.onclick = async () => {
      imp.disabled = true;
      try {
        const r = await api("/api/allowance/people/import", { method: "POST", body: {} });
        toast(L(`تم استيراد الكشف: ${r.created} جديد، ${r.updated} محدّث`, `Roster imported: ${r.created} new, ${r.updated} updated`));
        S.meta = null;
        render();
      } catch (e) { toast(e.message, "error"); imp.disabled = false; }
    };
    const btn = $("#al-new", view);
    if (btn) btn.onclick = newSheetDialog;
    const roster = $("#al-roster", view);
    if (roster) roster.onclick = rosterDialog;
  }

  function newSheetDialog() {
    const today = (APP.meta && APP.meta.today) || new Date().toISOString().slice(0, 10);
    const month = today.slice(0, 7);
    const departments = (S.meta && S.meta.departments) || [];
    const both = (S.meta && S.meta.all_departments) || departments.join(" + ");
    const workers = (S.meta && S.meta.people || []).filter(p => p.is_active).length;
    openForm({
      title: L("كشف إعاشة جديد", "New subsistence sheet"),
      submitLabel: L("إنشاء الكشف", "Create sheet"),
      fields: [
        { name: "scope", label: L("أقسام الكشف", "Sheet sections"), type: "text", readonly: true,
          value: L(both, both), span: 2,
          hint: L(`الكشف يشمل دائمًا «${departments.join("» و«")}» — ${workers} عاملًا في بيان واحد.`,
                  `The sheet always covers “${departments.join("” and “")}” — ${workers} workers in one statement.`) },
        { name: "start_date", label: L("بداية الفترة", "Period start"), type: "date", value: `${month}-01` },
        { name: "end_date", label: L("نهاية الفترة", "Period end"), type: "date", value: monthRange(month).end },
        { name: "att_from", label: L("أيام الحضور من (اختياري)", "Attendance from (optional)"), type: "date",
          hint: L("اتركه فارغًا = حضور كل أيام الفترة. مثال: من 01/10/2026 إلى 15/10/2026 ⇒ 15 يومًا.",
                  "Leave empty to count the whole period, e.g. 01/10 → 15/10 counts 15 days.") },
        { name: "att_to", label: L("أيام الحضور إلى (اختياري)", "Attendance to (optional)"), type: "date" },
        { name: "copy_roster", label: L("نسخ كشف العمال إلى الكشف", "Copy the roster into the sheet"),
          type: "select", value: "1", options: [{ value: "1", label: L("نعم", "Yes") }, { value: "0", label: L("لا (كشف فارغ)", "No (empty)") }] },
      ],
      onSubmit: async (data) => {
        const res = await api("/api/allowance/periods", {
          method: "POST",
          body: {
            department: "", start_date: data.start_date, end_date: data.end_date,
            copy_roster: data.copy_roster !== "0",
            att_from: data.att_from || null, att_to: data.att_to || null,
          },
        });
        location.hash = `#/allowance/${res.id}`;
      },
    });
  }

  /* ---------------------------------------------------------- roster */
  async function rosterDialog() {
    const data = await api("/api/allowance/people");
    const people = data.people || [];
    const byDept = {};
    people.forEach(p => { (byDept[p.department] = byDept[p.department] || []).push(p); });
    const body = `
      <div class="filters" style="margin-bottom:12px">
        <input id="rp-name" class="input" placeholder="${ESC(L("اسم العامل الجديد", "New worker name"))}" />
        <select id="rp-dept" class="input">${data.departments.map(d => `<option>${ESC(d)}</option>`).join("")}</select>
        <input id="rp-rate" class="input" type="number" step="0.01" value="30" style="max-width:110px" />
        <button class="btn btn-primary" id="rp-add">${icon("plus")} ${ESC(L("إضافة", "Add"))}</button>
      </div>
      ${Object.keys(byDept).map(dept => `
        <div class="section-title"><span>${ESC(dept)}</span><span class="line"></span></div>
        <div class="table-wrap"><table class="data compact">
          <thead><tr><th>${ESC(L("الاسم", "Name"))}</th><th>${ESC(L("الفئة/اليوم", "Daily rate"))}</th>
            <th>${ESC(L("الحالة", "Status"))}</th><th></th></tr></thead>
          <tbody>${byDept[dept].map(p => `<tr data-person="${p.id}">
            <td data-label="${ESC(L("الاسم", "Name"))}">${ESC(p.name_ar)}</td>
            <td data-label="${ESC(L("الفئة", "Rate"))}" class="num">${fmtMoney(p.daily_rate_cents)}</td>
            <td data-label="${ESC(L("الحالة", "Status"))}">${p.is_active ? ESC(L("نشط", "Active")) : ESC(L("موقوف", "Inactive"))}</td>
            <td class="act"><button class="btn btn-sm" data-toggle="${p.id}" data-active="${p.is_active ? 1 : 0}">
              ${p.is_active ? ESC(L("إيقاف", "Disable")) : ESC(L("تنشيط", "Enable"))}</button></td></tr>`).join("")}
          </tbody></table></div>`).join("")}
    `;
    openModal({
      title: L("كشف عمال الإعاشة", "Subsistence roster"), body, wide: true,
      onMount: (root) => {
        $("#rp-add", root).onclick = async () => {
          const name = $("#rp-name", root).value.trim();
          if (!name) return;
          await api("/api/allowance/people", { method: "POST", body: {
            name_ar: name, department: $("#rp-dept", root).value, daily_rate: $("#rp-rate", root).value } });
          toast(L("تمت الإضافة", "Added"));
          S.meta = null;
          $("#modal-root").innerHTML = "";
          rosterDialog();
        };
        $$("[data-toggle]", root).forEach(b => b.onclick = async () => {
          await api(`/api/allowance/people/${b.dataset.toggle}/active`, { method: "POST",
            body: { active: b.dataset.active === "1" ? false : true } });
          S.meta = null;
          $("#modal-root").innerHTML = "";
          rosterDialog();
        });
      },
    });
  }

  /* ---------------------------------------------------------- sheet */
  async function renderSheet(view, id) {
    const period = await api(`/api/allowance/periods/${id}`);
    S.period = period;
    const days = period.days;
    const startDay = period.start_day;
    setTitle(`${L("إعاشة", "Subsistence")} — ${period.department}`, ["الرواتب والإعاشة"]);
    const posted = period.status === "posted";
    const canManage = can("allowance.manage");
    const canPost = can("allowance.post");
    const editable = canManage && !posted;

    view.innerHTML = `
      <div class="page-head">
        <div>
          <h1>${ESC(L("إعاشة العاملين", "Staff subsistence allowance"))}</h1>
          <p>${ESC(L(`بيان بإعاشة العاملين — ${period.department} — الفترة ${dmy(period.start_date)} إلى ${dmy(period.end_date)} (${period.days} يومًا)`,
                     `Subsistence statement — ${period.department} — ${dmy(period.start_date)} to ${dmy(period.end_date)} (${period.days} days)`))}</p>
        </div>
        <div class="actions">
          <a class="btn btn-sm" href="#/allowance">${icon("chevron")} ${ESC(L("كل الكشوف", "All sheets"))}</a>
          <button class="btn btn-sm" id="al-grid">${icon("periods")} ${ESC(L("شبكة الأيام", "Day grid"))}</button>
          ${editable ? `<button class="btn btn-sm" id="al-window">${icon("calendar")} ${ESC(L("نطاق الحضور", "Attendance range"))}</button>` : ""}
          <button class="btn btn-sm" data-export="/api/allowance/periods/${id}/xlsx" data-name="allowance.xlsx">${icon("chart")} Excel</button>
          <button class="btn btn-sm" data-preview="/api/allowance/periods/${id}/pdf?lang=${APP.lang}">${icon("printer")} PDF</button>
          ${editable ? `<button class="btn btn-primary" id="al-save">${icon("check")} ${ESC(L("حفظ", "Save"))}</button>
            <details class="export-menu"><summary>${icon("menu")}</summary><div class="menu">
              <button id="al-del">${icon("trash")} ${ESC(L("حذف الكشف", "Delete sheet"))}</button>
            </div></details>` : ""}
          ${canPost && !posted ? `<button class="btn btn-sm" id="al-post">${icon("journal")} ${ESC(L("ترحيل محاسبي", "Post to ledger"))}</button>` : ""}
        </div>
      </div>

      <div class="grid al-kpis">
        ${kpi({ label: L("عدد العمال", "Workers"), value: `<span id="al-people">${period.totals.people_count}</span>`, raw: true, icon: icon("users") })}
        ${kpi({ label: L("أيام الإعاشة", "Allowance days"), value: `<span id="al-days">${period.totals.total_days}</span>`, raw: true, icon: icon("periods") })}
        ${kpi({ label: L("إجمالي الإعاشة", "Total allowance"), value: `<span id="al-total">${fmtMoney(period.totals.total_cents)}</span>`, raw: true, icon: icon("coins"), tone: "ok" })}
        ${kpi({ label: L("الحالة", "Status"), value: statusBadge(posted ? "posted" : "draft"), raw: true, icon: icon("check") })}
      </div>

      ${posted ? `<div class="card"><div class="card-body" style="display:flex;gap:10px;align-items:center">
          ${icon("lock") || ""} ${ESC(L("هذا الكشف مُرحَّل على الحسابات — لا يمكن تعديله. اعكس القيد من دفتر اليومية ثم أنشئ كشفًا جديدًا.",
                                       "This sheet is posted to the ledger and is read-only. Reverse the entry in the journal first."))}
        </div></div>` : ""}

      <div class="card"><div class="card-body tight">
        <div class="sheet-wrap">
          <table class="sheet" id="al-sheet">
            <thead>
              <tr class="sheet-title"><th colspan="${editable ? 15 : 14}">${ESC(L(`شركة سي سيرفيس للشحن والتفريغ والوكالة الملاحية — ${period.department}`,
                 `Sea Service Shipping & Stevedoring — ${period.department}`))}</th></tr>
              <tr class="sheet-date"><th colspan="${editable ? 15 : 14}">${ESC(L(`بيان بإعاشة العاملين عن الفترة من ${dmy(period.start_date)} حتى ${dmy(period.end_date)}`,
                 `Subsistence statement ${dmy(period.start_date)} — ${dmy(period.end_date)}`))}</th></tr>
              <tr>
                <th>${ESC(L("م", "No."))}</th>
                <th>${ESC(L("الفئة", "Rate"))}</th>
                <th class="name-col">${ESC(L("الاسم", "Name"))}</th>
                <th>${ESC(L("الحضور", "Present"))}</th>
                <th>${ESC(L("اعتيادى", "Annual"))}</th>
                <th>${ESC(L("مرضي", "Sick"))}</th>
                <th>${ESC(L("بدون مرتب", "Unpaid"))}</th>
                <th>${ESC(L("غياب", "Absent"))}</th>
                <th>${ESC(L("ماموريات", "Mission"))}</th>
                <th>${ESC(L("خصم إعاشة", "Ded. allow."))}</th>
                <th>${ESC(L("خصم مرتب", "Ded. salary"))}</th>
                <th>${ESC(L("الصافى", "Net days"))}</th>
                <th>${ESC(L("تطبيقات", "Adjustments"))}</th>
                <th>${ESC(L("إجمالى المستحق", "Total due"))}</th>
                ${editable ? `<th class="act">${ESC(L("حذف", "Del"))}</th>` : ""}
                ${Array.from({ length: days }, (_, i) => `<th class="day ${S.grid ? "" : "hidden"}">${startDay + i}</th>`).join("")}
              </tr>
            </thead>
            <tbody>
              ${sheetBody(period, days, editable, startDay)}
            </tbody>
            <tfoot>
              <tr class="sheet-total">
                <td colspan="3">${ESC(L("الإجمالي", "Total"))}</td>
                <td colspan="7"></td>
                <td class="num" id="al-total-days">${period.totals.total_days}</td>
                <td></td>
                <td class="num"><strong id="al-total-money">${fmtMoney(period.totals.total_cents)}</strong></td>
                <td></td>
                <td colspan="${days}" class="${S.grid ? "" : "hidden"}"></td>
              </tr>
              <tr class="sheet-sign">
                <td colspan="${3 + (editable ? 1 : 0)}">${ESC(L("توقيع المحاسب ،،،،", "Accountant signature"))}</td>
                <td colspan="${8}">${ESC(L("يعتمد ،،،", "Approved"))}</td>
                <td colspan="${days + 3}" class="${S.grid ? "" : "hidden"}"></td>
              </tr>
            </tfoot>
          </table>
        </div>
        <div class="sheet-legend">
          ${CODES.filter(c => c.code).map(c => `<span class="lg"><i class="${c.cls}">${ESC(c.code)}</i>${ESC(L(c.ar, c.en))}</span>`).join("")}
          <span class="lg muted">${ESC(L("اضغط على خانة اليوم لتغيير الرمز", "Tap a day cell to cycle the code"))}</span>
        </div>
      </div></div>
    `;
    wireSheet(view, period, editable, days);
  }

  /** The sheet is one statement holding both sections — render them as blocks. */
  function sheetBody(period, days, editable, startDay) {
    const cols = (editable ? 15 : 14) + days;
    const lines = period.lines || [];
    let sections = (period.totals && period.totals.sections) || [];
    if (!sections.length) {
      sections = [{ department: period.department || "", people_count: lines.length }];
    }
    const known = sections.map(x => x.department || "");
    const orphans = lines.filter(l => !known.includes(l.department || ""));
    if (orphans.length) sections = sections.concat([{ department: "", people_count: orphans.length }]);
    if (sections.length === 1 && !sections[0].department) {
      // single-section sheet: no header rows, just the rows
      return lines.map((line, idx) => lineRow(line, idx, days, editable, startDay, period)).join("");
    }
    return sections.map(sec => {
      const dept = sec.department || "";
      const group = lines.filter(l => (l.department || "") === dept);
      if (!group.length) return "";
      const head = `<tr class="sheet-section"><td colspan="${cols}">${ESC(dept || L("بدون قسم", "No section"))}
        <span class="sec-count">${L(`${group.length} عاملًا`, `${group.length} workers`)}</span></td></tr>`;
      const rows = group.map((line, idx) => lineRow(line, idx, days, editable, startDay, period)).join("");
      const sub = `<tr class="sheet-section-total" data-section="${ESC(dept)}">
          <td colspan="3">${ESC(L("إجمالي القسم", "Section total"))}</td>
          <td colspan="7"></td>
          <td class="num sec-days">${group.reduce((s, l) => s + num(l.net_days), 0)}</td>
          <td></td>
          <td class="num"><strong class="sec-money">${moneyLabel(group.reduce((s, l) => s + num(l.total_cents), 0))}</strong></td>
          <td></td>
          <td colspan="${days}" class="${S.grid ? "" : "hidden"}"></td>
        </tr>`;
      return head + rows + sub;
    }).join("");
  }

  function lineRow(line, idx, days, editable, startDay, period) {
    const codes = line.day_codes || {};
    const [winFrom, winTo] = windowOf(line, period);
    const cell = (field, extra = "") => editable
      ? `<input class="cell-input ${extra}" type="number" min="0" step="1" data-id="${line.id}" data-field="${field}" value="${line[field] ?? 0}" />`
      : `<span class="num">${line[field] ?? 0}</span>`;
    const moneyCell = editable
      ? `<input class="cell-input" type="number" step="0.01" data-id="${line.id}" data-field="adjustments" value="${(line.adjustments_cents / 100).toFixed(2)}" />`
      : `<span class="num">${fmtMoney(line.adjustments_cents)}</span>`;
    const rateCell = editable
      ? `<input class="cell-input w-rate" type="number" step="1" data-id="${line.id}" data-field="daily_rate" value="${rateOf(line)}" />`
      : `<span class="num">${rateOf(line)}</span>`;
    return `<tr data-line="${line.id}">
      <td class="num">${idx + 1}</td>
      <td class="num">${rateCell}</td>
      <td class="name-col">${editable
        ? `<input class="cell-input name-input" data-id="${line.id}" data-field="name_ar" value="${ESC(line.name_ar)}" />`
        : ESC(line.name_ar)}</td>
      <td class="num"><span class="calc" data-calc="present_days" data-id="${line.id}">${line.present_days}</span></td>
      <td class="num">${cell("leave_annual")}</td>
      <td class="num"><span class="calc" data-calc="leave_sick" data-id="${line.id}">${line.leave_sick}</span></td>
      <td class="num"><span class="calc" data-calc="leave_unpaid" data-id="${line.id}">${line.leave_unpaid}</span></td>
      <td class="num"><span class="calc" data-calc="absence_days" data-id="${line.id}">${line.absence_days}</span></td>
      <td class="num"><span class="calc" data-calc="mission_days" data-id="${line.id}">${line.mission_days}</span></td>
      <td class="num"><span class="calc" data-calc="ded_allowance_days" data-id="${line.id}">${line.ded_allowance_days}</span></td>
      <td class="num">${cell("ded_salary_days")}</td>
      <td class="num"><strong class="calc" data-calc="net_days" data-id="${line.id}">${line.net_days}</strong></td>
      <td class="num">${moneyCell}</td>
      <td class="num"><strong class="calc" data-calc="total" data-id="${line.id}">${fmtMoney(line.total_cents)}</strong></td>
      ${editable ? `<td class="act"><button class="btn btn-sm danger" data-del-line="${line.id}" title="${ESC(L("حذف السطر", "Delete row"))}">${icon("trash")}</button></td>` : ""}
      ${Array.from({ length: days }, (_, i) => {
        const day = i + 1;
        const code = codes[String(day)] || "";
        const off = day < winFrom || day > winTo;
        return `<td class="day ${S.grid ? "" : "hidden"} ${off ? "off" : ""}" data-day="${day}" data-id="${line.id}"
          data-code="${code}" data-off="${off ? 1 : 0}"
          title="${ESC(off ? L("خارج نطاق الحضور", "Outside the attendance range") : (CODE_LABEL[code] || ""))}"><span class="code ${CODE_CLS[code] || ""}">${code}</span></td>`;
      }).join("")}
    </tr>`;
  }

  function wireSheet(view, period, editable, days) {
    // day-grid toggle
    const gridBtn = $("#al-grid", view);
    if (gridBtn) gridBtn.onclick = () => {
      S.grid = !S.grid;
      $$(".day", view).forEach(el => el.classList.toggle("hidden", !S.grid));
      gridBtn.classList.toggle("btn-primary", S.grid);
    };
    if (S.grid) gridBtn && gridBtn.classList.add("btn-primary");

    const lineOf = (id) => period.lines.find(l => String(l.id) === String(id));

    const refresh = (id) => {
      const line = lineOf(id);
      if (!line) return;
      computePreview(line, days, period);
      $$(`[data-calc][data-id="${id}"]`, view).forEach(el => {
        const key = el.dataset.calc;
        el.textContent = key === "total" ? moneyLabel(line.total_cents) : line[key];
      });
      const tot = totals();
      const td = $("#al-total-days", view); if (td) td.textContent = tot.days;
      const tm = $("#al-total-money", view); if (tm) tm.textContent = moneyLabel(tot.cents);
      recalcFooter();
      markDirty();
    };

    if (editable) {
      $$("input.cell-input", view).forEach(inp => {
        inp.addEventListener("input", () => {
          const line = lineOf(inp.dataset.id);
          if (!line) return;
          const f = inp.dataset.field;
          line[f] = f === "name_ar" ? inp.value : num(inp.value);
          refresh(inp.dataset.id);
        });
      });
      $$("td.day", view).forEach(td => {
        td.onclick = () => {
          const line = lineOf(td.dataset.id);
          if (!line) return;
          if (td.dataset.off === "1") {
            toast(L("هذا اليوم خارج نطاق حضور العامل", "This day is outside the worker's attendance range"), "warn");
            return;
          }
          line.day_codes = line.day_codes || {};
          const order = CODES.map(c => c.code);
          const cur = line.day_codes[String(td.dataset.day)] || "";
          const next = order[(order.indexOf(cur) + 1) % order.length];
          if (next) line.day_codes[String(td.dataset.day)] = next;
          else delete line.day_codes[String(td.dataset.day)];
          td.dataset.code = next;
          td.querySelector(".code").textContent = next;
          td.querySelector(".code").className = "code " + (CODE_CLS[next] || "");
          td.title = CODE_LABEL[next] || "";
          refresh(td.dataset.id);
        };
      });
      $$("[data-del-line]", view).forEach(b => b.onclick = () => {
        const line = lineOf(b.dataset.delLine);
        confirmDialog(L(`حذف سطر «${line.name_ar}»؟`, `Delete row “${line.name_ar}”?`), () => {
          period.lines = period.lines.filter(l => String(l.id) !== String(b.dataset.delLine));
          b.closest("tr").remove();
          markDirty();
          recalcFooter();
        });
      });

      const win = $("#al-window", view);
      if (win) win.onclick = () => attendanceWindowDialog(period);

      const save = $("#al-save", view);
      if (save) save.onclick = () => savePeriod(period, save);

      const del = $("#al-del", view);
      if (del) del.onclick = () => confirmDialog(
        L("حذف الكشف بالكامل؟", "Delete the whole sheet?"), async () => {
          await api(`/api/allowance/periods/${period.id}`, { method: "DELETE" });
          toast(L("تم الحذف", "Deleted"));
          location.hash = "#/allowance";
        });
    }

    const post = $("#al-post", view);
    if (post) post.onclick = () => confirmDialog(
      L("سيتم إنشاء قيد محاسبي متوازن (مدين مصروف إعاشة / دائن مصروفات مستحقة) وترحيله. متابعة؟",
        "A balanced entry (Dr allowance expense / Cr accrued expenses) will be created and posted. Continue?"),
      async () => {
        if (S.dirty) await savePeriod(period, null, true);
        try {
          const r = await api(`/api/allowance/periods/${period.id}/post`, { method: "POST", body: {} });
          toast(L(`تم الترحيل — قيد رقم ${r.journal_entry_id}`, `Posted — entry #${r.journal_entry_id}`));
          render();
        } catch (e) { toast(e.message, "error"); }
      }, { danger: false, title: L("ترحيل محاسبي", "Post to ledger") });
  }

  /** «نطاق الحضور»: من تاريخ → إلى تاريخ، يتحسب ديناميكيًا لكل الصفوف أو لعامل واحد. */
  function attendanceWindowDialog(period) {
    const lines = period.lines || [];
    openForm({
      title: L("نطاق الحضور — من تاريخ إلى تاريخ", "Attendance range — from date to date"),
      submitLabel: L("تطبيق على الكشف", "Apply to the sheet"),
      fields: [
        { name: "att_from", label: L("الحضور من", "Attending from"), type: "date",
          value: period.start_date, hint: L("اترك الحقلين فارغين لإلغاء النطاق واحتساب كل أيام الفترة.",
                                            "Leave both empty to clear the range and count the whole period.") },
        { name: "att_to", label: L("الحضور إلى", "Attending to"), type: "date",
          value: period.end_date },
        { name: "target", label: L("تطبيق على", "Apply to"), type: "select", value: "all",
          options: [{ value: "all", label: L(`كل العمال (${lines.length})`, `All workers (${lines.length})`) },
                    ...lines.map(l => ({ value: String(l.id), label: l.name_ar }))] },
      ],
      onSubmit: async (data) => {
        // dates arrive as dd/mm/yyyy text — refuse a half-typed value instead of
        // letting the server read it as "no range" and wipe the configured one.
        // openForm hands dates over as ISO (yyyy-mm-dd); accept the typed dd/mm/yyyy form too
        const isDate = (v) => {
          const t = String(v || "").trim();
          const p = t.includes("/") ? t.split("/") : t.split("-");
          const year = p.length === 3 && (t.includes("/") ? p[2].length === 4 : p[0].length === 4);
          return !!year && p.every(x => x !== "" && !isNaN(Number(x)));
        };
        const bad = [["att_from", data.att_from], ["att_to", data.att_to]].find(([, v]) => v && !isDate(v));
        if (bad) {
          throw new Error(L(`صيغة التاريخ غير صحيحة في «${bad[0] === "att_from" ? "الحضور من" : "الحضور إلى"}» — استخدم يوم/شهر/سنة`,
                            `Invalid date in "${bad[0]}" — use dd/mm/yyyy`));
        }
        const clearing = !data.att_from && !data.att_to;
        const body = { att_from: data.att_from || null, att_to: data.att_to || null };
        if (data.target !== "all") body.line_ids = [Number(data.target)];
        const res = await api(`/api/allowance/periods/${period.id}/window`, { method: "POST", body });
        toast(clearing
          ? L(`تم إلغاء النطاق — ${res.total_days} يومًا، ${moneyLabel(res.total_cents)}`,
              `Range cleared — ${res.total_days} days, ${moneyLabel(res.total_cents)}`)
          : L(`تم ضبط نطاق الحضور — ${res.updated} صف، ${res.total_days} يومًا، ${moneyLabel(res.total_cents)}`,
              `Attendance range applied — ${res.updated} rows, ${res.total_days} days, ${moneyLabel(res.total_cents)}`));
        render();
      },
    });
  }

  async function savePeriod(period, btn, silent) {
    if (btn) { btn.disabled = true; }
    try {
      const body = {
        lines: period.lines.map(l => ({
          id: l.id, name_ar: l.name_ar, daily_rate: rateOf(l),
          att_from: l.att_from || null, att_to: l.att_to || null,
          leave_annual: l.leave_annual, ded_salary_days: l.ded_salary_days,
          adjustments: (l.adjustments_cents / 100).toFixed(2),
          leave_sick: l.leave_sick, leave_unpaid: l.leave_unpaid, absence_days: l.absence_days,
          mission_days: l.mission_days, ded_allowance_days: l.ded_allowance_days,
          day_codes: l.day_codes || {},
        })),
      };
      const res = await api(`/api/allowance/periods/${period.id}`, { method: "PUT", body });
      S.dirty = false;
      const money = moneyLabel(res.total_cents);
      if (!silent) toast(L(`تم الحفظ — ${res.people_count} عامل، ${money}`,
        `Saved — ${res.people_count} workers, ${money}`));
      // pick up server-side recomputation (authoritative)
      const fresh = await api(`/api/allowance/periods/${period.id}`);
      S.period = fresh;
      if (location.hash === `#/allowance/${period.id}`) render();
      return res;
    } catch (e) {
      toast(e.message, "error");
      throw e;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ---------------------------------------------------------- page */
  PAGES.allowance = {
    perm: "allowance.view",
    async render(view, params) {
      S.grid = localStorage.getItem("erp_allowance_grid") !== "0";
      if (params && params[0]) return renderSheet(view, params[0]);
      return renderList(view);
    },
  };
})();
