# Verification log

How each of the four requests was verified. No browser or rasteriser was available in the
build sandbox, so the UI was exercised with jsdom against the live server on `:8000`, plus a
CSS cascade analyser and a WCAG contrast calculator. **A human visual pass is still
recommended** — see the last section.

## Automated results

| Check | Command | Result |
| --- | --- | --- |
| Backend + API | `python3 -m unittest tests.test_erp tests.test_wsgi_bridge` | **75 tests, OK** (~20 s) |
| All pages render | `node render.js` | **24/24 pages** rendered |
| All statements render | `node statements.js` | **11/11 statements**, AR + EN |
| All report tabs | `node allreports.js` | **7/7 tabs**, each with a working export menu |
| Assistant | `node assistant.js`, `node encheck.js` | answers + cards + refs in AR and EN |
| Login flow | `node login.js`, `logininteract.js`, `demoall.js` | **6/6** demo accounts sign in; error paths correct |
| Contrast | `node contrast3.js`, `contrast4.js` | **0 of 14** token pairs below 4.5:1, both themes |
| CSS health | `node cssaudit.js` | 1215 rules, **0 parse errors** |
| Cascade @1440px | `node cascade.js` | components resolve to v4 tokens |
| Cascade @375px | `node cascade-mobile.js` | single-column, 16px inputs, drawer nav |

Export matrix (verified separately with `pypdf` and `openpyxl`): **44/44 → HTTP 200**.
PDFs carry `/Direction /R2L`, a `/Type0` font and a `/ToUnicode` map; workbooks are 31 rows /
110 cells with `rightToLeft="1"`.

## Two bugs worth remembering

**Reports page froze on its skeleton.** The export-menu builder destructured
`[fmt, lang, label, icon, hint]`. That parameter named `icon` shadowed the global `icon()`
helper for the whole callback, so `icon("chart")` threw `TypeError: icon is not a function`.
`PAGES.reports.render` awaits `load()` with no `try/catch`, so the page never replaced its
skeleton. Renamed to `glyph`.

**Payables did not reconcile.** Eleven posted payments of 15,000 each had only 10,000
allocated, leaving 11 × 5,000 = 55,000 stranded. The AP control account differed from the
vendor subledger by exactly that amount. The report now carries `unallocated_payments_cents`
and a `reconciles` flag asserting:

```
journal_balance_cents + unallocated_payments_cents == outstanding_cents
```

## What still needs a human

The sandbox cannot rasterise, so nothing here proves *pixel* correctness — only structure,
tokens, contrast, and behaviour. Please look at the live preview and check:

- the login screen, at desktop and on a phone;
- one statement in Arabic and the same one in English, including the printed page;
- the assistant conversation in both languages;
- the light/dark toggle.
