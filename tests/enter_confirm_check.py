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
    if edit_btns:
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
        check("confirm Enter → no error toast",
              page.query_selector(".toast.err") is None,
              (page.query_selector(".toast.err").inner_text()
               if page.query_selector(".toast.err") else ""))

    check("no page errors", not errors, errors)
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
