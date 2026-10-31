"""Enter in a confirmation dialog must press «تأكيد» — same as the mouse.

Run against a live server:
    python3 -m playwright install chromium
    ERP_BASE=http://127.0.0.1:8560 python3 tests/enter_confirm_check.py
"""

import json
import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("ERP_BASE", "http://127.0.0.1:8560")
USER = os.environ.get("ERP_USER", "ahmed.taranisi")
PASS = os.environ.get("ERP_PASS", "Eltaranisi@2026")

results = []


def check(name, ok, extra=""):
    results.append((name, ok, extra))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  ← {extra}"))


def open_confirm(page, danger=False):
    """Ask app.js for the real dialog the app uses, and record its answer."""
    page.evaluate(
        """(danger) => {
             window.__answer = 'pending';
             window.confirmAsync('هل أنت متأكد من تنفيذ الاختبار؟', { danger });
             window.confirmAsync('هل أنت متأكد من تنفيذ الاختبار؟', { danger })
               .then(v => { window.__answer = v; });
           }""",
        danger,
    )
    page.wait_for_selector("[data-cf-yes]")
    page.wait_for_timeout(120)


def answer(page):
    return page.evaluate("window.__answer")


def dialog_open(page):
    return page.evaluate(
        "() => { const r = document.querySelector('#modal-root');"
        " return !!r && !r.classList.contains('hidden'); }"
    )


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1360, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    page.goto(BASE + "/", wait_until="networkidle")
    page.fill("#login-username", USER)
    page.fill("#login-password", PASS)
    page.click("#login-form button[type=submit]")
    page.wait_for_selector("#login-screen", state="hidden", timeout=20000)
    page.wait_for_timeout(800)

    # 1) Enter accepts the dialog
    open_confirm(page)
    check("dialog is on screen", dialog_open(page))
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    check("Enter → confirm returns true", answer(page) is True, answer(page))
    check("Enter → dialog closed", not dialog_open(page))

    # 2) Enter on the focused «إلغاء» button cancels (native button semantics kept)
    open_confirm(page)
    page.focus("[data-cf-no]")
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    check("Enter on إلغاء → false", answer(page) is False, answer(page))

    # 3) mouse click still works exactly as before
    open_confirm(page)
    page.click("[data-cf-yes]")
    page.wait_for_timeout(250)
    check("mouse click on تأكيد → true", answer(page) is True, answer(page))

    # 4) a danger dialog (delete) behaves the same
    open_confirm(page, danger=True)
    page.keyboard.press("Enter")
    page.wait_for_timeout(250)
    check("danger dialog: Enter → true", answer(page) is True, answer(page))

    # 5) the reported flow: edit a debt item (form → Enter → confirm → Enter → saved)
    page.goto(BASE + "/#/debt", wait_until="networkidle")
    page.wait_for_timeout(1500)
    edit_btns = page.query_selector_all("[data-ed-path]")
    check("debt screen has an edit button", bool(edit_btns), len(edit_btns))
    original = None
    if edit_btns:
        # remember the record so the check leaves the data exactly as it found it
        original = page.evaluate(
            """() => {
                 const b = document.querySelector('[data-ed-path]');
                 return { path: b.dataset.edPath,
                          fields: JSON.parse(decodeURIComponent(b.dataset.edFields || '%5B%5D')) };
               }"""
        )
        edit_btns[0].click()
        page.wait_for_timeout(700)
        check("edit form opened", page.query_selector("#gen-form") is not None)
        # make a real change first (the form refuses to save an untouched record),
        # then Enter inside a text field → the confirmation appears
        target = page.evaluate(
            """() => {
                 const inputs = [...document.querySelectorAll('#gen-form input, #gen-form textarea, #gen-form select')];
                 const el = inputs.find(i => !i.disabled && i.type !== 'hidden' && i.tagName === 'INPUT') || inputs[0];
                 if (!el) return null;
                 if (el.tagName === 'SELECT') { el.selectedIndex = (el.selectedIndex + 1) % el.options.length; }
                 else if (el.type !== 'checkbox') { el.value = (el.value || '') + '‏'; }
                 else { el.checked = !el.checked; }
                 el.dispatchEvent(new Event('input', { bubbles: true }));
                 el.dispatchEvent(new Event('change', { bubbles: true }));
                 el.focus();
                 return el.name || el.id || el.tagName;
               }"""
        )
        check("edit form has a field to change", bool(target), target)
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)
        check("form Enter → confirmation dialog", page.query_selector("[data-cf-yes]") is not None)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        check("confirm Enter → dialog closed", not dialog_open(page))
        err_toast = page.query_selector(".toast.err")
        check("confirm Enter → no error toast", err_toast is None,
              err_toast.inner_text() if err_toast else "")
        # put the original values back through the API (audited, like any edit)
        if original:
            restored = page.evaluate(
                """async ({ path, fields }) => {
                     const body = {};
                     for (const f of fields) body[f.name] = f.value;
                     const res = await fetch(path, { method: 'PATCH',
                       headers: { 'Content-Type': 'application/json',
                                  'Authorization': 'Bearer ' + localStorage.getItem('erp_token') },
                       body: JSON.stringify(body) });
                     return res.status;
                   }""",
                original,
            )
            check("original values restored", restored in (200, 204), restored)

    # 6) the delete dialog: the message must be plain readable text (no leaked
    #    HTML) and Enter must press «اعتماد» — exactly like the mouse.
    #    Creating a throwaway row is only done against a local server.
    destructive = ("127.0.0.1" in BASE) or ("localhost" in BASE)
    page.goto(BASE + "/#/debt", wait_until="networkidle")
    page.wait_for_timeout(1200)
    item = None
    if destructive:
        item = page.evaluate(
            """async () => {
                 const tok = localStorage.getItem('erp_token');
                 const accs = await (await fetch('/api/debt/accounts', { headers: { Authorization: 'Bearer ' + tok } })).json();
                 const acc = (accs.accounts || [])[0];
                 const res = await fetch('/api/debt/items', { method: 'POST',
                   headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok },
                   body: JSON.stringify({ account_id: acc.id, description: 'اختبار زر Enter للحذف',
                                          amount: 13014, post: false,
                                          doc_date: (acc.created_at || '').slice(0, 10) || undefined }) });
                 const body = await res.json();
                 return res.ok ? { id: body.id, acc: acc.id } : null;
               }"""
        )
        check("throwaway debt item created", bool(item), item)
        if item:
            page.goto(f"{BASE}/#/debt/{item['acc']}", wait_until="networkidle")
            page.wait_for_timeout(1500)
            trash = page.query_selector(f'[data-del-path="/api/debt/items/{item["id"]}"]')
    else:
        trash = page.query_selector("[data-del-path]")      # an existing row: cancel, never confirm
    check("delete button found", trash is not None)
    if trash:
        trash.click()
        page.wait_for_timeout(600)
        text = page.inner_text("#modal-root")
        check("delete dialog is on screen", page.query_selector("[data-cf-yes], #cf-ok") is not None)
        check("message is plain text (no HTML leaks)",
              "<span" not in text and "class=" not in text, text[:140])
        check("accept button takes focus (Enter is one key away)",
              page.evaluate("() => document.activeElement && document.activeElement.hasAttribute('data-modal-confirm')"))
        if destructive and item:
            check("message keeps the amount", "13,014.00" in text, text[:140])
            page.keyboard.press("Enter")
            page.wait_for_timeout(1800)
            gone = page.evaluate(
                """async (id) => {
                     const tok = localStorage.getItem('erp_token');
                     const res = await fetch('/api/debt/items/' + id, { headers: { Authorization: 'Bearer ' + tok } });
                     return res.status;
                   }""",
                item["id"],
            )
            check("Enter deleted the row", gone == 404, gone)
            check("delete dialog closed", not dialog_open(page))
        else:
            page.click("[data-cf-no]")
            page.wait_for_timeout(300)
            check("cancel on this host leaves the data untouched", not dialog_open(page))

    check("no page errors", not errors, errors)
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
