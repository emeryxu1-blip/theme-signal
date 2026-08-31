import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

import worker, { normalizeThemeName } from '../src/worker.ts';

const initialMigration = readFileSync(new URL('../migrations/0001_initial.sql', import.meta.url), 'utf8');
const themeKeyMigration = readFileSync(new URL('../migrations/0002_unique_theme_key.sql', import.meta.url), 'utf8');
const themeAliasMigration = readFileSync(new URL('../migrations/0003_theme_aliases.sql', import.meta.url), 'utf8');

function applyInitialMigration() {
  const db = new DatabaseSync(':memory:');
  db.exec(initialMigration);
  return db;
}

function applyAllMigrations() {
  const db = applyInitialMigration();
  db.exec(themeKeyMigration);
  db.exec(themeAliasMigration);
  return db;
}

function d1Adapter(db: DatabaseSync) {
  return {
    prepare(sql: string) {
      const statement = db.prepare(sql);
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              return statement.get(...values as never[]) ?? null;
            },
            async run() {
              const result = statement.run(...values as never[]);
              return { success: true, meta: { changes: Number(result.changes) } };
            },
            async all() {
              return { success: true, results: statement.all(...values as never[]) };
            },
          };
        },
      };
    },
  };
}

function insertLegacyTheme(
  db: DatabaseSync,
  { id, createdAt, theme, hash }: { id: string; createdAt: string; theme: string; hash: string },
) {
  const raw = JSON.stringify({ theme });
  db.prepare(`INSERT INTO themes
    (id,created_at,event_date,theme,source_url,cover_url,stock_count,etf_count,content_sha256,byte_length,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)`).run(
    id, createdAt, '2026-08-13', theme, null, null, 0, 0, hash, raw.length, raw,
  );
}

// The production migration collapses existing name duplicates onto the most
// recent row while keeping every older public ID as a working alias.
{
  const db = applyInitialMigration();
  insertLegacyTheme(db, {
    id: 'theme_d534216027f2c40062cea042',
    createdAt: '2026-08-13T10:00:00.000Z',
    theme: 'Neocloud',
    hash: '1'.repeat(64),
  });
  insertLegacyTheme(db, {
    id: 'new-neocloud',
    createdAt: '2026-08-13T11:00:00.000Z',
    theme: '  neocloud  ',
    hash: '2'.repeat(64),
  });
  insertLegacyTheme(db, {
    id: 'inflation',
    createdAt: '2026-08-12T10:00:00.000Z',
    theme: 'Inflation Cooling',
    hash: '3'.repeat(64),
  });

  db.exec(themeKeyMigration);

  assert.deepEqual(
    db.prepare('SELECT id, theme_key FROM themes ORDER BY id').all().map(row => ({ ...row })),
    [
      { id: 'inflation', theme_key: 'inflation cooling' },
      { id: 'new-neocloud', theme_key: 'neocloud' },
    ],
  );
  assert.deepEqual(
    db.prepare('SELECT alias_id, theme_id FROM theme_aliases').all().map(row => ({ ...row })),
    [{
      alias_id: 'theme_d534216027f2c40062cea042',
      theme_id: 'new-neocloud',
    }],
  );
  assert.throws(
    () => db.prepare("UPDATE themes SET theme_key = 'inflation cooling' WHERE id = 'new-neocloud'").run(),
    /UNIQUE constraint failed/,
  );

  const env = {
    DB: d1Adapter(db),
    ASSETS: { fetch: async () => new Response('not reached') },
    INGEST_TOKEN: 'test-token',
  } as never;
  const replacementRaw = JSON.stringify({
    theme: 'Neocloud',
    date: '2026-08-14',
    ThemeStocks: [{ market_code: '185:NEW' }, { market_code: '185:NEW2' }],
    ThemeEtfs: [{ market_code: '186:ETF' }],
  });
  const replacement = await worker.fetch(
    new Request('https://example.test/api/themes', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-theme-upload-token': 'test-token',
      },
      body: replacementRaw,
    }),
    env,
  );
  assert.equal(replacement.status, 200);
  assert.deepEqual(await replacement.json(), {
    id: 'new-neocloud',
    duplicate: false,
    replaced: true,
  });
  const legacyDetail = await worker.fetch(
    new Request('https://example.test/api/themes/theme_d534216027f2c40062cea042'),
    env,
  );
  assert.equal(legacyDetail.status, 200);
  assert.equal(await legacyDetail.text(), replacementRaw);
  db.close();
}

assert.deepEqual(normalizeThemeName('  NEOcloud  '), {
  displayName: 'NEOcloud',
  key: 'neocloud',
});
assert.deepEqual(normalizeThemeName('Ｎｅｏ   ｃｌｏｕｄ'), {
  displayName: 'Neo cloud',
  key: 'neo cloud',
});
assert.equal(normalizeThemeName('   '), null);
assert.equal(normalizeThemeName(null), null);

// Exercise the real Worker handler and SQL against SQLite, whose constraints
// and UPSERT behavior match D1.
{
  const db = applyAllMigrations();
  const env = {
    DB: d1Adapter(db),
    ASSETS: { fetch: async () => new Response('not reached') },
    INGEST_TOKEN: 'test-token',
  } as never;
  const post = (raw: string, extraHeaders: Record<string, string> = {}) => worker.fetch(
    new Request('https://example.test/api/themes', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-theme-upload-token': 'test-token',
        ...extraHeaders,
      },
      body: raw,
    }),
    env,
  );

  const oldRaw = JSON.stringify({
    theme: 'Neocloud',
    date: '2026-08-13',
    url: 'https://example.test/old',
    ThemeStocks: [{ market_code: '185:OLD' }],
    ThemeEtfs: [],
  });
  const firstResponse = await post(oldRaw);
  const first = await firstResponse.json() as { id: string; duplicate: boolean; replaced: boolean };
  assert.equal(firstResponse.status, 201);
  assert.match(first.id, /^theme_[a-f0-9]{24}$/);
  assert.deepEqual({ duplicate: first.duplicate, replaced: first.replaced }, {
    duplicate: false,
    replaced: false,
  });

  const retryResponse = await post(oldRaw);
  assert.equal(retryResponse.status, 200);
  assert.deepEqual(await retryResponse.json(), {
    id: first.id,
    duplicate: true,
    replaced: false,
  });

  const newRaw = JSON.stringify({
    theme: '  nEoClOuD  ',
    date: '2026-08-14',
    url: 'https://example.test/new',
    cover: 'https://example.test/new.jpg',
    ThemeStocks: [{ market_code: '185:NEW' }, { market_code: '185:NEW2' }],
    ThemeEtfs: [{ market_code: '186:ETF' }],
  });
  const replacementResponse = await post(newRaw);
  const replacement = await replacementResponse.json() as {
    id: string; duplicate: boolean; replaced: boolean;
  };
  assert.equal(replacementResponse.status, 200);
  assert.equal(replacement.id, first.id);
  assert.deepEqual({ duplicate: replacement.duplicate, replaced: replacement.replaced }, {
    duplicate: false,
    replaced: true,
  });

  assert.equal(db.prepare('SELECT count(*) AS count FROM themes').get()?.count, 1);
  assert.deepEqual(
    { ...db.prepare(`SELECT id,event_date,theme,theme_key,source_url,cover_url,stock_count,etf_count,raw_json
      FROM themes`).get() },
    {
      id: replacement.id,
      event_date: '2026-08-14',
      theme: 'nEoClOuD',
      theme_key: 'neocloud',
      source_url: 'https://example.test/new',
      cover_url: 'https://example.test/new.jpg',
      stock_count: 2,
      etf_count: 1,
      raw_json: newRaw,
    },
  );

  const oldDetail = await worker.fetch(
    new Request(`https://example.test/api/themes/${first.id}`),
    env,
  );
  assert.equal(oldDetail.status, 200);
  assert.equal(oldDetail.headers.get('cache-control'), 'no-store');
  assert.equal(await oldDetail.text(), newRaw);

  const listResponse = await worker.fetch(
    new Request('https://example.test/api/themes'),
    env,
  );
  assert.deepEqual(await listResponse.json(), {
    themes: [{
      id: first.id,
      created_at: db.prepare('SELECT created_at FROM themes WHERE id = ?').get(first.id)?.created_at,
      event_date: '2026-08-14',
      theme: 'nEoClOuD',
      source_url: 'https://example.test/new',
      cover_url: 'https://example.test/new.jpg',
      stock_count: 2,
      etf_count: 1,
    }],
  });

  const otherRaw = JSON.stringify({ theme: 'Inflation Cooling', ThemeStocks: [], ThemeEtfs: [] });
  const otherResponse = await post(otherRaw);
  assert.equal(otherResponse.status, 201);
  assert.equal(db.prepare('SELECT count(*) AS count FROM themes').get()?.count, 2);

  const blankResponse = await post(JSON.stringify({ theme: '   ' }));
  assert.equal(blankResponse.status, 422);
  assert.deepEqual(await blankResponse.json(), {
    error: 'Result must include a non-empty theme name',
  });
  assert.equal(db.prepare('SELECT count(*) AS count FROM themes').get()?.count, 2);

  const badHashResponse = await post(
    JSON.stringify({ theme: 'Another Theme' }),
    { 'x-content-sha256': '0'.repeat(64) },
  );
  assert.equal(badHashResponse.status, 422);
  assert.equal(db.prepare('SELECT count(*) AS count FROM themes').get()?.count, 2);
  db.close();
}

console.log('ingest ok');
