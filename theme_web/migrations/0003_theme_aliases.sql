-- Production may already have the original 0002 migration recorded. Add the
-- alias schema idempotently so stable theme URLs work in both upgrade paths.
CREATE TABLE IF NOT EXISTS theme_aliases (
  alias_id TEXT PRIMARY KEY,
  theme_id TEXT NOT NULL,
  FOREIGN KEY (theme_id) REFERENCES themes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS theme_aliases_theme_id_idx
  ON theme_aliases(theme_id);
