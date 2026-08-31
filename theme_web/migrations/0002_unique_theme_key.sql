ALTER TABLE themes ADD COLUMN theme_key TEXT;

-- Existing archive names are English theme titles. Normalize their case and
-- surrounding whitespace before collapsing historical duplicates.
UPDATE themes
SET theme_key = lower(trim(theme));

-- Keep the newest historical row for each key while preserving every older
-- public ID as an alias. rowid breaks ties when timestamps are identical.
CREATE TABLE theme_aliases (
  alias_id TEXT PRIMARY KEY,
  theme_id TEXT NOT NULL,
  FOREIGN KEY (theme_id) REFERENCES themes(id) ON DELETE CASCADE
);

INSERT INTO theme_aliases (alias_id, theme_id)
SELECT older.id, newest.id
FROM themes AS older
JOIN themes AS newest
  ON newest.theme_key = older.theme_key
 AND newest.id <> older.id
WHERE NOT EXISTS (
  SELECT 1
  FROM themes AS candidate
  WHERE candidate.theme_key = newest.theme_key
    AND (
      candidate.created_at > newest.created_at
      OR (candidate.created_at = newest.created_at AND candidate.rowid > newest.rowid)
    )
);

DELETE FROM themes
WHERE id IN (SELECT alias_id FROM theme_aliases);

CREATE UNIQUE INDEX themes_theme_key_unique_idx ON themes(theme_key);
CREATE INDEX theme_aliases_theme_id_idx ON theme_aliases(theme_id);
