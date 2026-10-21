#!/usr/bin/env python3
"""Mobile / small-screen audit — proves the app never scrolls sideways.

Runs a real headless Chromium over every page at phone and tablet widths, in both
languages and both themes, and fails if **any** page scrolls horizontally or if
any content is clipped off the right/left edge.

    python3 tools/mobile_audit.py                      # needs a server on :8000
    ERP_BASE=http://127.0.0.1:8080 python3 tools/mobile_audit.py
    python3 tools/mobile_audit.py --widths 320,360,390 --shots /tmp/shots

Setup (once):

    pip install playwright && python3 -m playwright install --with-deps chromium

Exit code 0 = clean, 1 = at least one page overflows.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

PAGES = ["dashboard", "accounts", "journal", "reports", "sales", "purchases", "payments",
         "debt", "payables", "receivables", "customers", "vendors", "inventory", "banks",
         "assets", "payroll", "budgets", "settings", "users", "periods", "audit", "assistant"]

# Any element sticking out of the viewport is a problem *unless* it lives inside
# its own scroller (that is a deliberate inner scroll area) or is hidden /
# off-canvas (the closed sidebar, a closed panel …).
PROBE = """
() => {
  const vw = document.documentElement.clientWidth;
  const inScroller = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ox = getComputedStyle(p).overflowX;
      if (['auto', 'scroll', 'hidden', 'clip'].includes(ox)) return true;
    }
    return false;
  };
  const isHidden = (el) => {
    for (let p = el; p && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return true;
      const r = p.getBoundingClientRect();
      if (r.right <= 0 || r.left >= vw) return true;
    }
    return false;
  };
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.right <= vw + 1.5 && r.left >= -1.5) continue;   // sub-pixel tolerance
    if (inScroller(el) || isHidden(el)) continue;
    bad.push(el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(' ').slice(0, 2).join(' ')
             + ' [' + Math.round(r.left) + '..' + Math.round(r.right) + '] '
             + (el.textContent || '').trim().slice(0, 30));
  }
  return { sw: document.documentElement.scrollWidth, vw, bad: bad.slice(0, 6) };
}
"""


async def audit(widths, langs, themes, shots, user, password):
    from playwright.async_api import async_playwright

    base = os.environ.get("ERP_BASE", "http://127.0.0.1:8000").rstrip("/")
    problems = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for lang in langs:
            for theme in themes:
                for width in widths:
                    ctx = await browser.new_context(
                        viewport={"width": width, "height": 820}, device_scale_factor=2,
                        is_mobile=width < 900, has_touch=width < 900, locale="ar-EG")
                    page = await ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    # Pin the appearance explicitly: the product default is dark, so
                    # a plain visit would already be dark (the toggle only flips it).
                    await page.add_init_script(
                        "try { localStorage.setItem('erp_theme_choice', '%s');"
                        " localStorage.setItem('erp_lang', '%s'); } catch (e) {}"
                        % (theme, lang))
                    await page.goto(base, wait_until="networkidle")
                    got_theme = await page.evaluate("()=>document.documentElement.dataset.theme")
                    if got_theme != theme:
                        print(f"! theme mismatch: wanted {theme}, got {got_theme}")
                    # --- login screen itself (it was invisible to earlier audits) ---
                    login_info = await page.evaluate(PROBE)
                    card = await page.evaluate(
                        "()=>{const c=document.querySelector('.login-card');"
                        "if(!c) return null; const r=c.getBoundingClientRect();"
                        "return {w:Math.round(r.width), l:Math.round(r.left), r:Math.round(r.right), vw:innerWidth};}")
                    login_bad = []
                    if login_info["sw"] > login_info["vw"] + 1 or login_info["bad"]:
                        login_bad.append(("login-overflow", login_info))
                    # the card must use most of a phone screen, not be squeezed into a sliver
                    # a healthy card fills >=75% of the phone width (the bug this
                    # guard was written for left it at ~36% of a 360px screen)
                    if card and card["vw"] <= 640 and (
                            card["w"] < card["vw"] * 0.75 or card["w"] < 240):
                        login_bad.append(("login-card-too-narrow", card))
                    if shots:
                        os.makedirs(shots, exist_ok=True)
                        await page.screenshot(
                            path=os.path.join(shots, f"{lang}-{theme}-{width}-login.png"))
                    if login_bad:
                        problems += len(login_bad)
                        print(f"✗ {lang}/{theme}/{width}px login screen:")
                        for tag, info in login_bad:
                            print(f"    {tag}: {info}")

                    await page.fill("#login-username", user)
                    await page.fill("#login-password", password)
                    await page.click("#login-form button[type=submit]")
                    await page.wait_for_timeout(2200)
                    bad_pages = []
                    for name in PAGES:
                        await page.evaluate("(n) => { location.hash = '#/' + n; }", name)
                        await page.wait_for_timeout(520)
                        info = await page.evaluate(PROBE)
                        if info["sw"] > info["vw"] + 1 or info["bad"]:
                            bad_pages.append((name, info))
                        if shots:
                            os.makedirs(shots, exist_ok=True)
                            await page.screenshot(
                                path=os.path.join(shots, f"{lang}-{theme}-{width}-{name}.png"))
                    label = f"{lang}/{theme}/{'mobile' if width < 900 else 'desktop'} {width}px"
                    if bad_pages:
                        problems += len(bad_pages)
                        print(f"✗ {label}: {len(bad_pages)} page(s) overflow")
                        for name, info in bad_pages:
                            print(f"    {name}: scrollWidth={info['sw']} vs {info['vw']}")
                            for b in info["bad"]:
                                print(f"       {b}")
                    else:
                        print(f"✓ {label}: login + {len(PAGES)} pages, no horizontal scroll, "
                              f"{len(errors)} JS errors")
                    await ctx.close()
        await browser.close()
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--widths", default="320,360,390,414,768,1024")
    ap.add_argument("--langs", default="ar,en")
    ap.add_argument("--themes", default="light,dark")
    ap.add_argument("--shots", default="")
    ap.add_argument("--user", default=os.environ.get("ERP_AUDIT_USER", "ahmed.taranisi"))
    ap.add_argument("--password", default=os.environ.get("ERP_AUDIT_PASSWORD", "Eltaranisi@2026"))
    args = ap.parse_args()
    widths = [int(w) for w in args.widths.split(",") if w.strip()]
    problems = asyncio.run(audit(widths, args.langs.split(","), args.themes.split(","),
                                 args.shots, args.user, args.password))
    print(f"\n{'✓ no horizontal scrolling anywhere' if not problems else f'✗ {problems} problem block(s)'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
