CREATE TABLE IF NOT EXISTS themes (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  event_date TEXT,
  theme TEXT NOT NULL,
  source_url TEXT,
  cover_url TEXT,
  stock_count INTEGER NOT NULL DEFAULT 0,
  etf_count INTEGER NOT NULL DEFAULT 0,
  content_sha256 TEXT NOT NULL UNIQUE,
  byte_length INTEGER NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS themes_created_at_idx ON themes(created_at DESC);
CREATE INDEX IF NOT EXISTS themes_theme_idx ON themes(theme);
