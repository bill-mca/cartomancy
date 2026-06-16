-- Cartomancy backend — D1 (SQLite) initial schema.  Migration 0001.
--
-- Reconciles docs/PLAN.md §4.2 with the plugin's ACTUAL auth: an activation key
-- (tl_<32 hex>) sent as `Authorization: Bearer …` + `X-Product-ID: ai-edit`,
-- validated at GET /api/plugin/usage and minted via the browser pairing flow.
-- (The PLAN's schema assumed email/password; the plugin has neither.)
--
-- Apply with:  wrangler d1 migrations apply <DB_NAME>

PRAGMA foreign_keys = ON;

-- Accounts. email is nullable until a plan/checkout links one.
CREATE TABLE users (
    id          TEXT PRIMARY KEY,             -- UUID v4
    email       TEXT UNIQUE,
    created_at  INTEGER NOT NULL,             -- unix epoch (seconds)
    plan        TEXT NOT NULL DEFAULT 'free'  -- 'free' | 'pro' | 'studio'
);

-- Activation keys = the plugin's Bearer credential. Store only a SHA-256 hash;
-- the plaintext tl_ key lives solely on the user's machine. Validate by hashing
-- the incoming Bearer token and looking it up here.
CREATE TABLE activation_keys (
    key_hash     TEXT PRIMARY KEY,            -- sha256(hex) of the tl_ key
    user_id      TEXT NOT NULL REFERENCES users(id),
    product_id   TEXT NOT NULL DEFAULT 'ai-edit',
    active       INTEGER NOT NULL DEFAULT 1,  -- 0 = revoked/expired
    created_at   INTEGER NOT NULL,
    last_used_at INTEGER
);
CREATE INDEX idx_activation_keys_user ON activation_keys(user_id);

-- Credit ledger / quota. Columns mirror the /api/plugin/usage response the
-- plugin parses (images_used, images_limit, is_free_tier) so the route is a
-- direct row read.
CREATE TABLE credits (
    user_id      TEXT PRIMARY KEY REFERENCES users(id),
    balance      INTEGER NOT NULL DEFAULT 75,   -- credits remaining (PLAN §6)
    images_used  INTEGER NOT NULL DEFAULT 0,
    images_limit INTEGER NOT NULL DEFAULT 75,   -- free tier: 75 lifetime credits
    is_free_tier INTEGER NOT NULL DEFAULT 1,
    reset_at     INTEGER                         -- epoch for monthly reset; NULL on free
);

-- Browser pairing handoff: the plugin mints a code client-side
-- (secrets.token_urlsafe), opens /connect?code=…, and polls
-- /api/plugin/pair/poll until the signed-in user binds it to a key.
CREATE TABLE pairing_codes (
    code        TEXT PRIMARY KEY,             -- client-minted opaque code
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|ready|cancelled|no_plan
    key_hash    TEXT REFERENCES activation_keys(key_hash),
    product_id  TEXT NOT NULL DEFAULT 'ai-edit',
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);

-- Generation audit log (PLAN §4.2) keyed by the request_id the plugin polls.
-- Print-layout context (dpi, paper size) now flows from the composer engine (M1)
-- and the layout capture (M2).
CREATE TABLE generations (
    id              TEXT PRIMARY KEY,         -- request_id
    user_id         TEXT REFERENCES users(id),
    created_at      INTEGER NOT NULL,
    resolution      TEXT NOT NULL,            -- '1K' | '2K' | '4K'
    credits_used    INTEGER NOT NULL,
    dpi             INTEGER,                  -- from the print layout
    paper_width_mm  REAL,
    paper_height_mm REAL,
    prompt          TEXT,
    r2_key          TEXT,                     -- object path in R2
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|completed|failed|refunded
    error_code      TEXT
);
CREATE INDEX idx_generations_user_created ON generations(user_id, created_at);
