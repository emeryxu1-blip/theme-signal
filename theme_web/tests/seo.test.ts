import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import worker from '../src/worker.ts';
import {
  archiveCardMarkup,
  archiveJsonLd,
  detailJsonLd,
  escapeHtml,
  safeJson,
  safeHttpUrl,
  themeDescription,
} from '../src/seo.ts';
import { readFileSync } from 'node:fs';

const initialMigration = readFileSync(new URL('../migrations/0001_initial.sql', import.meta.url), 'utf8');
const themeKeyMigration = readFileSync(new URL('../migrations/0002_unique_theme_key.sql', import.meta.url), 'utf8');
const aliasesMigration = readFileSync(new URL('../migrations/0003_theme_aliases.sql', import.meta.url), 'utf8');

function dbAdapter(db: DatabaseSync) {
  return {
    prepare(sql: string) {
      const statement = db.prepare(sql);
      return {
        bind(...values: unknown[]) {
          return {
            async first() { return statement.get(...values as never[]) ?? null; },
            async all() { return { results: statement.all(...values as never[]) }; },
          };
        },
      };
    },
  };
}

function makeDb() {
  const db = new DatabaseSync(':memory:');
  db.exec(initialMigration);
  db.exec(themeKeyMigration);
  db.exec(aliasesMigration);
  return db;
}

const row = {
  id: 'theme_<script>',
  created_at: '2026-08-24T00:00:00.000Z',
  event_date: '2026-08-20',
  theme: 'AI & Chips <Now>',
  source_url: 'https://example.test/source',
  cover_url: 'http://example.test/cover.jpg',
  stock_count: 8,
  etf_count: 5,
};
const card = archiveCardMarkup(row, 'https://example.test', 0);
assert.match(card, /<a class="theme-card theme-card-featured" href="https:\/\/example\.test\/themes\/theme_%3Cscript%3E"/);
assert.match(card, /AI &amp; Chips &lt;Now&gt;/);
assert.doesNotMatch(card, /8 stocks|5 ETFs|stock_count|etf_count|View theme|theme-card-open/);
assert.equal(safeHttpUrl('javascript:alert(1)'), '');
assert.equal(safeHttpUrl('http://example.test/x', 'https://example.test'), 'https://example.test/x');
assert.match(escapeHtml('"&<>'), /&quot;&amp;&lt;&gt;/);
assert.doesNotMatch(`<script>${safeJson({ value: '</script><script>alert(1)</script>' })}</script>`, /<\/script><script>/);
assert.match(themeDescription({ theme: 'AI' }), /Research context for AI/);
assert.equal((archiveJsonLd([row], 'https://example.test') as any)['@context'], 'https://schema.org');
assert.equal((detailJsonLd({ theme: 'AI', ThemeFAQ: [{ question: 'What?', answer: 'A.' }] }, row, 'https://example.test') as any)['@graph'].some((item: any) => item['@type'] === 'FAQPage'), true);

const db = makeDb();
const raw = JSON.stringify({
  theme: 'AI & Chips <Now>',
  date: '2026-08-20',
  cover: 'https://example.test/cover.jpg',
  url: 'https://example.test/source',
  ThemeStocks: [{ market_code: '185:NVDA', 'Theme exposure': 4.8 }],
  ThemeEtfs: [{ market_code: '169:SOXX', 'Theme exposure': 4.1 }],
  ThemeFAQ: [{ question: 'What is this?', answer: 'A factual research context.' }],
});
const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
const hex = [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('');
db.prepare(`INSERT INTO themes (id,created_at,event_date,theme,theme_key,source_url,cover_url,stock_count,etf_count,content_sha256,byte_length,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
  'theme_current', '2026-08-24T00:00:00.000Z', '2026-08-20', 'AI & Chips <Now>', 'ai & chips <now>', 'https://example.test/source', 'https://example.test/cover.jpg', 1, 1, hex, raw.length, raw,
);
const env = { DB: dbAdapter(db), ASSETS: { fetch: async () => new Response('asset') } } as never;

const home = await worker.fetch(new Request('https://example.test/'), env);
assert.equal(home.status, 200);
const homeText = await home.text();
assert.match(homeText, /Trending Market Themes/);
assert.match(homeText, /href="https:\/\/example\.test\/themes\/theme_current"/);
assert.match(homeText, /application\/ld\+json/);
assert.doesNotMatch(homeText, /1 stocks|1 ETFs/);
assert.match(homeText, /__THEMESIGNAL_BOOT__/);

const detail = await worker.fetch(new Request('https://example.test/themes/theme_current'), env);
assert.equal(detail.status, 200);
const detailText = await detail.text();
assert.match(detailText, /<h1>AI &amp; Chips &lt;Now&gt;<\/h1>/);
assert.match(detailText, /rel="canonical" href="https:\/\/example\.test\/themes\/theme_current"/);
assert.match(detailText, /FAQPage/);
assert.match(detailText, /A factual research context\./);

const missing = await worker.fetch(new Request('https://example.test/themes/not-here'), env);
assert.equal(missing.status, 404);
assert.match(missing.headers.get('x-robots-tag') || '', /noindex/);
assert.match(await missing.text(), /Theme not found/);

const robots = await worker.fetch(new Request('https://example.test/robots.txt'), env);
const robotsText = await robots.text();
assert.match(robotsText, /Disallow: \/api\//);
assert.match(robotsText, /Sitemap/);

const sitemap = await worker.fetch(new Request('https://example.test/sitemap.xml'), env);
const sitemapText = await sitemap.text();
assert.match(sitemapText, /<loc>https:\/\/example\.test\/themes\/theme_current<\/loc>/);
assert.match(sitemapText, /<lastmod>2026-08-20<\/lastmod>/);

db.close();
console.log('seo ok');
