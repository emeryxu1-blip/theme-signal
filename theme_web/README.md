# ThemeSignal

**Market Theme Signals — Investment Themes, Stocks & ETFs**

Cloudflare Worker Static Assets + D1 website for generated theme-investing results.

## Local setup

```bash
npm install
npx wrangler d1 create theme-signals
# Put the returned database_id in wrangler.jsonc
npx wrangler d1 migrations apply theme-signals --local
npx wrangler secret put INGEST_TOKEN
npm run dev
```

For production, apply migrations with `--remote`, set `INGEST_TOKEN`, then configure the optional live-data cookie without printing it:

```bash
npx wrangler secret put QUOTE_C_COOKIE
# paste the existing C-side cookie only when prompted; never commit it or put it in public assets
npm run deploy
```

The Worker accepts the exact compact JSON produced by the local workflow at `POST /api/themes` with the upload token. D1 stores the raw body unchanged and deduplicates by SHA-256. D1 free-tier rows are limited in size; this implementation rejects payloads over 2 MB. Use D1 metadata + R2 raw objects if results outgrow that limit.

When `QUOTE_C_COOKIE` is configured, the Worker proxies normalized live data through `/api/themes/:id/live`, `/quotes`, and `/exposure-map`. The cookie is sent only on the server-side upstream request and is never returned to browsers. Live responses are cached briefly and return `Live market data unavailable` when the upstream is missing or unavailable. The original `/api/themes/:id` response remains byte-for-byte raw JSON.

Production URL: `https://theme-signal-archive.emery-xu1.workers.dev/`

The internal quote and language-model providers are implementation details and are not part of the public product identity.

*Educational tooling, not investment advice.*
