-- =====================================================================
-- ELTARANISI ERP — Database Schema (SQLite / PostgreSQL-compatible core)
-- All monetary amounts are stored as INTEGER minor units (cents/piastres)
-- to avoid floating point rounding errors in financial calculations.
-- All quantities are stored as INTEGER thousandths (qty * 1000).
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Organization / tenancy
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS organizations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id          INTEGER NOT NULL REFERENCES organizations(id),
    code            TEXT NOT NULL UNIQUE,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    base_currency   TEXT NOT NULL DEFAULT 'EGP',
    tax_number      TEXT,
    address         TEXT,
    phone           TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS branches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    code            TEXT NOT NULL,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (company_id, code)
);

-- ---------------------------------------------------------------------
-- Users / roles / sessions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    permissions     TEXT NOT NULL DEFAULT '[]',   -- JSON array
    data_scope      TEXT NOT NULL DEFAULT 'company', -- company | own_company | read_only | platform
    is_system       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER REFERENCES companies(id),
    branch_id       INTEGER REFERENCES branches(id),
    username        TEXT NOT NULL UNIQUE,
    full_name_ar    TEXT NOT NULL,
    full_name_en    TEXT NOT NULL,
    email           TEXT,
    password_hash   TEXT NOT NULL,
    role_code       TEXT NOT NULL REFERENCES roles(code),
    language        TEXT NOT NULL DEFAULT 'ar',
    theme           TEXT NOT NULL DEFAULT 'light',
    is_active       INTEGER NOT NULL DEFAULT 1,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT,
    password_must_change INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at   TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token           TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL,
    ip              TEXT,
    user_agent      TEXT,
    revoked         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_company_access (
    user_id         INTEGER NOT NULL REFERENCES users(id),
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    PRIMARY KEY (user_id, company_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER,
    user_id         INTEGER,
    username        TEXT,
    action          TEXT NOT NULL,
    entity          TEXT,
    entity_id       INTEGER,
    details         TEXT,
    ip              TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- Fiscal calendar
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fiscal_years (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    name            TEXT NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    UNIQUE (company_id, name)
);

CREATE TABLE IF NOT EXISTS fiscal_periods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    fiscal_year_id  INTEGER NOT NULL REFERENCES fiscal_years(id),
    period_no       INTEGER NOT NULL,
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    closed_by       INTEGER REFERENCES users(id),
    closed_at       TEXT,
    reopened_by     INTEGER REFERENCES users(id),
    reopened_at     TEXT,
    UNIQUE (fiscal_year_id, period_no)
);

-- ---------------------------------------------------------------------
-- Chart of accounts
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    code            TEXT NOT NULL,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','expense')),
    parent_id       INTEGER REFERENCES accounts(id),
    level           INTEGER NOT NULL DEFAULT 1,
    is_postable     INTEGER NOT NULL DEFAULT 1,
    is_active       INTEGER NOT NULL DEFAULT 1,
    currency        TEXT NOT NULL DEFAULT 'EGP',
    closing_type    TEXT NOT NULL DEFAULT 'balance',  -- balance | period
    notes           TEXT,
    created_by      INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS cost_centers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    code            TEXT NOT NULL,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    code            TEXT NOT NULL,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    cost_center_id  INTEGER REFERENCES cost_centers(id),
    status          TEXT NOT NULL DEFAULT 'active',
    UNIQUE (company_id, code)
);

-- ---------------------------------------------------------------------
-- Journal entries (double entry core)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS journal_entries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    branch_id           INTEGER REFERENCES branches(id),
    entry_no            TEXT NOT NULL,
    entry_date          TEXT NOT NULL,
    entry_type          TEXT NOT NULL DEFAULT 'manual',
    memo                TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','review','approved','posted','reversed')),
    currency            TEXT NOT NULL DEFAULT 'EGP',
    fx_rate             REAL NOT NULL DEFAULT 1,
    total_debit_cents   INTEGER NOT NULL DEFAULT 0 CHECK (total_debit_cents >= 0),
    total_credit_cents  INTEGER NOT NULL DEFAULT 0 CHECK (total_credit_cents >= 0),
    cost_center_id      INTEGER REFERENCES cost_centers(id),
    project_id          INTEGER REFERENCES projects(id),
    source_type         TEXT,
    source_id           INTEGER,
    reversal_of_id      INTEGER REFERENCES journal_entries(id),
    reversed_by_id      INTEGER REFERENCES journal_entries(id),
    idempotency_key     TEXT UNIQUE,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    submitted_by        INTEGER REFERENCES users(id),
    submitted_at        TEXT,
    approved_by         INTEGER REFERENCES users(id),
    approved_at         TEXT,
    posted_by           INTEGER REFERENCES users(id),
    posted_at           TEXT,
    UNIQUE (company_id, entry_no),
    -- a posted entry must be balanced: enforced at DB level too
    CHECK (status <> 'posted' OR total_debit_cents = total_credit_cents),
    CHECK (status <> 'reversed' OR total_debit_cents = total_credit_cents)
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    line_no         INTEGER NOT NULL,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    debit_cents     INTEGER NOT NULL DEFAULT 0 CHECK (debit_cents >= 0),
    credit_cents    INTEGER NOT NULL DEFAULT 0 CHECK (credit_cents >= 0),
    description     TEXT,
    cost_center_id  INTEGER REFERENCES cost_centers(id),
    project_id      INTEGER REFERENCES projects(id),
    customer_id     INTEGER,
    vendor_id       INTEGER,
    foreign_amount  INTEGER,
    CHECK ((debit_cents > 0 AND credit_cents = 0) OR (credit_cents > 0 AND debit_cents = 0)),
    UNIQUE (entry_id, line_no)
);

CREATE INDEX IF NOT EXISTS idx_lines_entry ON journal_lines(entry_id);
CREATE INDEX IF NOT EXISTS idx_lines_account ON journal_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_entries_company_date ON journal_entries(company_id, entry_date);
CREATE INDEX IF NOT EXISTS idx_entries_status ON journal_entries(company_id, status);

-- ---------------------------------------------------------------------
-- Parties
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    code                TEXT NOT NULL,
    name_ar             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    phone               TEXT,
    email               TEXT,
    tax_number          TEXT,
    address             TEXT,
    credit_limit_cents  INTEGER NOT NULL DEFAULT 0,
    payment_terms_days  INTEGER NOT NULL DEFAULT 30,
    account_id          INTEGER REFERENCES accounts(id),
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS vendors (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    code                TEXT NOT NULL,
    name_ar             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    phone               TEXT,
    email               TEXT,
    tax_number          TEXT,
    address             TEXT,
    payment_terms_days  INTEGER NOT NULL DEFAULT 30,
    account_id          INTEGER REFERENCES accounts(id),
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, code)
);

-- ---------------------------------------------------------------------
-- Inventory masters
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    branch_id       INTEGER REFERENCES branches(id),
    code            TEXT NOT NULL,
    name_ar         TEXT NOT NULL,
    name_en         TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS products (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id              INTEGER NOT NULL REFERENCES companies(id),
    sku                     TEXT NOT NULL,
    name_ar                 TEXT NOT NULL,
    name_en                 TEXT NOT NULL,
    unit                    TEXT NOT NULL DEFAULT 'قطعة',
    category                TEXT,
    is_service              INTEGER NOT NULL DEFAULT 0,
    sale_price_cents        INTEGER NOT NULL DEFAULT 0,
    purchase_price_cents    INTEGER NOT NULL DEFAULT 0,
    tax_rate_bp             INTEGER NOT NULL DEFAULT 1400,  -- basis points (14.00%)
    reorder_level_milli     INTEGER NOT NULL DEFAULT 0,
    valuation_method        TEXT NOT NULL DEFAULT 'weighted_average'
                            CHECK (valuation_method IN ('weighted_average','fifo')),
    inventory_account_id    INTEGER REFERENCES accounts(id),
    cogs_account_id         INTEGER REFERENCES accounts(id),
    revenue_account_id      INTEGER REFERENCES accounts(id),
    is_active               INTEGER NOT NULL DEFAULT 1,
    UNIQUE (company_id, sku)
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    product_id      INTEGER NOT NULL REFERENCES products(id),
    warehouse_id    INTEGER NOT NULL REFERENCES warehouses(id),
    move_date       TEXT NOT NULL,
    move_type       TEXT NOT NULL CHECK (move_type IN ('in','out','transfer_in','transfer_out','adjust')),
    qty_milli       INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL DEFAULT 0,
    value_cents     INTEGER NOT NULL DEFAULT 0,
    running_qty_milli INTEGER NOT NULL DEFAULT 0,
    running_value_cents INTEGER NOT NULL DEFAULT 0,
    ref_type        TEXT,
    ref_id          INTEGER,
    journal_entry_id INTEGER REFERENCES journal_entries(id),
    notes           TEXT,
    created_by      INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stock_product ON stock_movements(company_id, product_id, move_date);

-- ---------------------------------------------------------------------
-- Sales
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    branch_id           INTEGER REFERENCES branches(id),
    invoice_no          TEXT NOT NULL,
    customer_id         INTEGER NOT NULL REFERENCES customers(id),
    invoice_date        TEXT NOT NULL,
    due_date            TEXT NOT NULL,
    warehouse_id        INTEGER REFERENCES warehouses(id),
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    discount_cents      INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    paid_cents          INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','posted','partially_paid','paid','cancelled')),
    journal_entry_id    INTEGER REFERENCES journal_entries(id),
    notes               TEXT,
    idempotency_key     TEXT UNIQUE,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    posted_at           TEXT,
    UNIQUE (company_id, invoice_no)
);

CREATE TABLE IF NOT EXISTS sales_invoice_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id          INTEGER NOT NULL REFERENCES sales_invoices(id) ON DELETE CASCADE,
    line_no             INTEGER NOT NULL,
    product_id          INTEGER REFERENCES products(id),
    description         TEXT,
    qty_milli           INTEGER NOT NULL,
    unit_price_cents    INTEGER NOT NULL,
    discount_cents      INTEGER NOT NULL DEFAULT 0,
    tax_rate_bp         INTEGER NOT NULL DEFAULT 0,
    line_subtotal_cents INTEGER NOT NULL,
    line_tax_cents      INTEGER NOT NULL,
    line_total_cents    INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- Purchases
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS purchase_invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    branch_id           INTEGER REFERENCES branches(id),
    invoice_no          TEXT NOT NULL,
    supplier_ref        TEXT,
    vendor_id           INTEGER NOT NULL REFERENCES vendors(id),
    invoice_date        TEXT NOT NULL,
    due_date            TEXT NOT NULL,
    warehouse_id        INTEGER REFERENCES warehouses(id),
    po_number           TEXT,
    grn_number          TEXT,
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    discount_cents      INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    paid_cents          INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','posted','partially_paid','paid','cancelled')),
    three_way_match     INTEGER NOT NULL DEFAULT 0,
    journal_entry_id    INTEGER REFERENCES journal_entries(id),
    notes               TEXT,
    idempotency_key     TEXT UNIQUE,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    posted_at           TEXT,
    UNIQUE (company_id, invoice_no)
);

CREATE TABLE IF NOT EXISTS purchase_invoice_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id          INTEGER NOT NULL REFERENCES purchase_invoices(id) ON DELETE CASCADE,
    line_no             INTEGER NOT NULL,
    product_id          INTEGER REFERENCES products(id),
    description         TEXT,
    qty_milli           INTEGER NOT NULL,
    unit_price_cents    INTEGER NOT NULL,
    discount_cents      INTEGER NOT NULL DEFAULT 0,
    tax_rate_bp         INTEGER NOT NULL DEFAULT 0,
    line_subtotal_cents INTEGER NOT NULL,
    line_tax_cents      INTEGER NOT NULL,
    line_total_cents    INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- Payments / receipts / allocations
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    branch_id           INTEGER REFERENCES branches(id),
    payment_no          TEXT NOT NULL,
    payment_type        TEXT NOT NULL CHECK (payment_type IN ('receipt','payment','expense','employee_advance')),
    payment_date        TEXT NOT NULL,
    party_type          TEXT CHECK (party_type IN ('customer','vendor','none')),
    party_id            INTEGER,
    method              TEXT NOT NULL CHECK (method IN ('cash','bank')),
    gl_account_id       INTEGER NOT NULL REFERENCES accounts(id),
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    allocated_cents     INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted','cancelled')),
    reference           TEXT,
    notes               TEXT,
    journal_entry_id    INTEGER REFERENCES journal_entries(id),
    idempotency_key     TEXT UNIQUE,
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    posted_at           TEXT,
    UNIQUE (company_id, payment_no)
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id      INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    invoice_kind    TEXT NOT NULL CHECK (invoice_kind IN ('sales','purchase')),
    invoice_id      INTEGER NOT NULL,
    amount_cents    INTEGER NOT NULL CHECK (amount_cents > 0)
);
CREATE INDEX IF NOT EXISTS idx_alloc_invoice ON payment_allocations(invoice_kind, invoice_id);

-- ---------------------------------------------------------------------
-- Banks & cash
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bank_accounts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    code                TEXT NOT NULL,
    name_ar             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    account_type        TEXT NOT NULL CHECK (account_type IN ('bank','cash')),
    gl_account_id       INTEGER NOT NULL REFERENCES accounts(id),
    iban                TEXT,
    currency            TEXT NOT NULL DEFAULT 'EGP',
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    is_active           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    bank_account_id     INTEGER NOT NULL REFERENCES bank_accounts(id),
    txn_date            TEXT NOT NULL,
    description         TEXT NOT NULL,
    debit_cents         INTEGER NOT NULL DEFAULT 0,
    credit_cents        INTEGER NOT NULL DEFAULT 0,
    statement_ref       TEXT,
    matched_payment_id  INTEGER REFERENCES payments(id),
    is_reconciled       INTEGER NOT NULL DEFAULT 0,
    reconciled_by       INTEGER REFERENCES users(id),
    reconciled_at       TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------
-- Fixed assets & depreciation
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fixed_assets (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    code                TEXT NOT NULL,
    name_ar             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    category            TEXT,
    purchase_date       TEXT NOT NULL,
    start_date          TEXT NOT NULL,
    cost_cents          INTEGER NOT NULL,
    salvage_cents       INTEGER NOT NULL DEFAULT 0,
    useful_life_months  INTEGER NOT NULL,
    method              TEXT NOT NULL DEFAULT 'straight_line',
    accumulated_cents   INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disposed')),
    asset_account_id    INTEGER REFERENCES accounts(id),
    dep_account_id      INTEGER REFERENCES accounts(id),
    expense_account_id  INTEGER REFERENCES accounts(id),
    disposed_at         TEXT,
    proceeds_cents      INTEGER,
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS depreciation_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    run_no              TEXT NOT NULL,
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted')),
    total_cents         INTEGER NOT NULL DEFAULT 0,
    journal_entry_id    INTEGER REFERENCES journal_entries(id),
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, run_no)
);

CREATE TABLE IF NOT EXISTS depreciation_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES depreciation_runs(id) ON DELETE CASCADE,
    asset_id        INTEGER NOT NULL REFERENCES fixed_assets(id),
    amount_cents    INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- HR & payroll
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS employees (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    code                TEXT NOT NULL,
    name_ar             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    department          TEXT,
    job_title           TEXT,
    hire_date           TEXT NOT NULL,
    basic_salary_cents  INTEGER NOT NULL,
    allowances_cents    INTEGER NOT NULL DEFAULT 0,
    deductions_cents    INTEGER NOT NULL DEFAULT 0,
    insurance_cents     INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    bank_name           TEXT,
    iban                TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS payroll_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    run_no              TEXT NOT NULL,
    period              TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted')),
    gross_cents         INTEGER NOT NULL DEFAULT 0,
    deductions_cents    INTEGER NOT NULL DEFAULT 0,
    net_cents           INTEGER NOT NULL DEFAULT 0,
    journal_entry_id    INTEGER REFERENCES journal_entries(id),
    created_by          INTEGER REFERENCES users(id),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, run_no)
);

CREATE TABLE IF NOT EXISTS payroll_lines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
    employee_id     INTEGER NOT NULL REFERENCES employees(id),
    basic_cents     INTEGER NOT NULL,
    allowances_cents INTEGER NOT NULL DEFAULT 0,
    deductions_cents INTEGER NOT NULL DEFAULT 0,
    net_cents       INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- Budgets
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS budgets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    fiscal_year_id  INTEGER NOT NULL REFERENCES fiscal_years(id),
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    cost_center_id  INTEGER REFERENCES cost_centers(id),
    period_month    INTEGER NOT NULL CHECK (period_month BETWEEN 1 AND 12),
    budget_cents    INTEGER NOT NULL,
    notes           TEXT,
    UNIQUE (company_id, fiscal_year_id, account_id, cost_center_id, period_month)
);

-- ---------------------------------------------------------------------
-- Notifications / approvals / settings / attachments / FX
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    user_id         INTEGER REFERENCES users(id),
    type            TEXT NOT NULL DEFAULT 'info',
    title_ar        TEXT NOT NULL,
    title_en        TEXT NOT NULL,
    body_ar         TEXT,
    body_en         TEXT,
    link            TEXT,
    is_read         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);

CREATE TABLE IF NOT EXISTS approval_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    entity              TEXT NOT NULL DEFAULT 'journal_entry',
    min_amount_cents    INTEGER NOT NULL DEFAULT 0,
    max_amount_cents    INTEGER,
    required_role       TEXT NOT NULL,
    level               INTEGER NOT NULL DEFAULT 1,
    is_active           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS approval_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL,
    entity          TEXT NOT NULL,
    entity_id       INTEGER NOT NULL,
    level           INTEGER NOT NULL,
    required_role   TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    actor_id        INTEGER REFERENCES users(id),
    comment         TEXT,
    acted_at        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies(id),
    key         TEXT NOT NULL,
    value       TEXT,
    UNIQUE (company_id, key)
);

CREATE TABLE IF NOT EXISTS attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    entity          TEXT NOT NULL,
    entity_id       INTEGER NOT NULL,
    file_name       TEXT NOT NULL,
    mime_type       TEXT,
    size_bytes      INTEGER,
    storage_path    TEXT NOT NULL,
    uploaded_by     INTEGER REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS exchange_rates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    from_currency   TEXT NOT NULL,
    to_currency     TEXT NOT NULL,
    rate            REAL NOT NULL CHECK (rate > 0),
    rate_date       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',
    UNIQUE (company_id, from_currency, to_currency, rate_date)
);

-- Document numbering (per company / branch / year / document type)
CREATE TABLE IF NOT EXISTS document_sequences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    doc_type        TEXT NOT NULL,
    year            INTEGER NOT NULL,
    last_number     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (company_id, doc_type, year)
);

-- ---------------------------------------------------------------------
-- Reporting views
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_posted_lines AS
SELECT l.id            AS line_id,
       e.id            AS entry_id,
       e.company_id    AS company_id,
       e.branch_id     AS branch_id,
       e.entry_date    AS entry_date,
       e.entry_no      AS entry_no,
       e.entry_type    AS entry_type,
       l.account_id    AS account_id,
       l.debit_cents   AS debit_cents,
       l.credit_cents  AS credit_cents,
       l.cost_center_id AS cost_center_id,
       l.project_id    AS project_id,
       l.customer_id   AS customer_id,
       l.vendor_id     AS vendor_id,
       l.description   AS description
FROM journal_lines l
JOIN journal_entries e ON e.id = l.entry_id
WHERE e.status IN ('posted','reversed');


-- ---------------------------------------------------------------------------
-- E-invoicing readiness ledger: one immutable receipt per issued document.
-- Stores the UUID + SHA-256 digest of the canonical JSON payload so a document
-- can be re-verified later. Transmission to a tax authority is NOT performed.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS einvoice_documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    INTEGER NOT NULL REFERENCES companies(id),
    invoice_kind  TEXT NOT NULL CHECK (invoice_kind IN ('sales','purchase')),
    invoice_id    INTEGER NOT NULL,
    document_uuid TEXT NOT NULL,
    digest        TEXT NOT NULL,
    payload       TEXT NOT NULL,
    issued_by     INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (invoice_kind, invoice_id)
);


-- ---------------------------------------------------------------------------
-- Debt / مديونية — contractor & supplier accounts billed by quantity
-- (طن، نقلة، حاوية، ساعة، إيجار شهري، بند). Mirrors the paper debt statement:
-- a per-account statement built from مستحقات (debt_items) minus سدادات
-- (debt_payments, each carrying an اذن صرف number).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS debt_accounts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id            INTEGER NOT NULL REFERENCES companies(id),
    code                  TEXT NOT NULL,
    name_ar               TEXT NOT NULL,
    name_en               TEXT NOT NULL DEFAULT '',
    kind                  TEXT NOT NULL DEFAULT 'contractor'
                          CHECK (kind IN ('contractor','labor','equipment','transport','fuel','workshop','supplies','other')),
    billing_unit          TEXT NOT NULL DEFAULT 'item'
                          CHECK (billing_unit IN ('ton','trip','container','hour','month','item')),
    vendor_id             INTEGER REFERENCES vendors(id),
    expense_account_id    INTEGER REFERENCES accounts(id),
    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
    is_active             INTEGER NOT NULL DEFAULT 1,
    notes                 TEXT,
    created_by            INTEGER REFERENCES users(id),
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, code)
);

CREATE TABLE IF NOT EXISTS debt_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL REFERENCES companies(id),
    account_id        INTEGER NOT NULL REFERENCES debt_accounts(id),
    item_no           TEXT NOT NULL,
    doc_date          TEXT NOT NULL,
    description       TEXT NOT NULL,
    operation         TEXT,
    qty_milli         INTEGER NOT NULL DEFAULT 0,
    unit              TEXT NOT NULL DEFAULT 'item',
    unit_rate_cents   INTEGER NOT NULL DEFAULT 0,
    amount_cents      INTEGER NOT NULL,
    vessel            TEXT,
    client_name       TEXT,
    cargo             TEXT,
    period_from       TEXT,
    period_to         TEXT,
    report_no         TEXT,
    status            TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted')),
    journal_entry_id  INTEGER REFERENCES journal_entries(id),
    notes             TEXT,
    created_by        INTEGER REFERENCES users(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS debt_payments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       INTEGER NOT NULL REFERENCES companies(id),
    account_id       INTEGER NOT NULL REFERENCES debt_accounts(id),
    payment_no       TEXT NOT NULL,
    voucher_no       TEXT,
    pay_date         TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL,
    method           TEXT NOT NULL DEFAULT 'cash',
    gl_account_id    INTEGER REFERENCES accounts(id),
    payee            TEXT,
    status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','posted')),
    journal_entry_id INTEGER REFERENCES journal_entries(id),
    notes            TEXT,
    created_by       INTEGER REFERENCES users(id),
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_debt_items_account ON debt_items(company_id, account_id, doc_date);
CREATE INDEX IF NOT EXISTS idx_debt_pay_account ON debt_payments(company_id, account_id, pay_date);
CREATE INDEX IF NOT EXISTS idx_debt_accounts_kind ON debt_accounts(company_id, kind);
