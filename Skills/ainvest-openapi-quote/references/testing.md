# Testing

Use this file when manually validating the skill or reproducing requests during development.

## Access Scenes

Choose the scene from user context before making a live call.

### Sandbox Aime Claw

Use when the user is working in the sandbox/Aime Claw environment or no B/C context is provided.

- `https://open.ainvest.com/market/extquote/index/indicator/v2/snapshot`
- `https://open.ainvest.com/market/extquote/index/indicator/v2/series`
- `https://open.ainvest.com/market/extquote/index/relation/v1/list`
- `https://open.ainvest.com/market/extquote/ag/quote/v2/multi_kline`
- `https://open.ainvest.com/market/extquote/ag/quote/v2/single_tick`
- auth header: `Authorization: Bearer <AIME_API_KEY>`
- default header: `X-Auth-ProgId: 7080`
- `fetch_quote.py` scene: `--scene sandbox`

### B-side

Use when the user mentions B-side, internal gateway, apisix, service-to-service calls, or `quote-apisix-gateway.hxapisix`.

- `http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/snapshot`
- `http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/series`
- `http://quote-apisix-gateway.hxapisix/index_api/relation/v1/list`
- `http://quote-apisix-gateway.hxapisix/quote/v2/multi_kline`
- `http://quote-apisix-gateway.hxapisix/quote/v2/single_tick`
- auth header: `apikey: <caller-provided apikey>`
- default header: `X-Auth-ProgId: 7080`
- `fetch_quote.py` scene: `--scene b`

B-side apikey values are application-scoped:

- `index_api/indicator/v2/snapshot`, `index_api/indicator/v2/series`, and `index_api/relation/v1/list` usually use the `index-api` app apikey.
- `quote/v2/multi_kline` and `quote/v2/single_tick` usually use the `quoteag` app apikey.
- Do not assume the two apikey values are interchangeable.
- Require only the apikey for the endpoint family being called. Do not ask for `quoteag` when only calling indicator or relation endpoints, and do not ask for `index-api` when only calling quote endpoints.

### C-side

Use when the user mentions C-side, browser/client/web access, cookie auth, or public quote domains.

- `https://extquote.ainvest.com/index_api/indicator/v2/snapshot`
- `https://extquote.ainvest.com/index_api/indicator/v2/series`
- `https://extquote.ainvest.com/index_api/relation/v1/list`
- `https://quote.ainvest.com/quote/v2/multi_kline`
- `https://quote.ainvest.com/quote/v2/single_tick`
- auth header: `Cookie: <caller-provided cookie>`
- default header: `X-Auth-ProgId: 7080`
- `fetch_quote.py` scene: `--scene c`

`Accept-Language` is optional and defaults to `en` if omitted. `X-Auth-ProgId` defaults to `7080` and should be sent on every request.

Keep test credentials out of repository prose. Do not surface API keys or auth identifiers by default in normal user-facing answers.

## Header precedence for live fetches

When the user wants actual quote or relation data instead of only a request body:

1. Choose the scene from user context.
2. For sandbox, check whether `AIME_API_KEY` is available.
3. For B-side or C-side, extract the caller-provided apikey or Cookie from the current request/context. For B-side, choose the apikey by endpoint family: `index-api` for indicator and relation endpoints and `quoteag` for quote endpoints.
4. If auth is available, construct the request and call the API directly.
5. Compose the scene-specific auth header.
6. If auth is missing, do not claim a live fetch succeeded; fall back to request construction and explain the missing env/header.

## Recommended answer shape for live fetches

When a live fetch succeeds:

1. State that live data was fetched.
2. State which endpoint was used.
3. State the scene and auth header shape used, without exposing the secret value.
4. Summarize the key returned values instead of dumping the full raw response.
5. Mention null values, missing indicators, or empty series only when they affect interpretation.

When a live fetch fails:

1. State that the request was attempted but failed.
2. Include the returned `status_code` / `status_msg` when available.
3. Classify the issue as auth, request-parameter, or data-side.
4. Provide the request body or missing header note needed for retry.

For concrete successful response examples, see `references/live-fetch-examples.md`.

## Example sandbox curl shape

```bash
curl --request POST \
  --url https://open.ainvest.com/market/extquote/ag/quote/v2/multi_kline \
  --header 'Accept-Language: zh-hans' \
  --header 'X-Auth-ProgId: 7080' \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer <AIME_API_KEY>' \
  --data '{...}'
```

## Example B-side curl shape

```bash
curl --request POST \
  --url http://quote-apisix-gateway.hxapisix/quote/v2/multi_kline \
  --header 'Accept-Language: zh-hans' \
  --header 'X-Auth-ProgId: 7080' \
  --header 'Content-Type: application/json' \
  --header 'apikey: <caller-provided-apikey>' \
  --data '{...}'
```

## Example C-side curl shape

```bash
curl --request POST \
  --url https://quote.ainvest.com/quote/v2/multi_kline \
  --header 'Accept-Language: zh-hans' \
  --header 'X-Auth-ProgId: 7080' \
  --header 'Content-Type: application/json' \
  --header 'Cookie: <caller-provided-cookie>' \
  --data '{...}'
```

## Verified scenario sources

Use the request templates in `assets/request-templates/` together with `references/scenarios.md` as the primary local scenario sources in this repository. Together they cover:

- `snapshot` with `market_code`
- `snapshot` with `ths_code`
- `snapshot` with `block_id`
- `snapshot` with `prompt_id`
- `snapshot` with `market`
- `snapshot` with `link_code`
- `snapshot` with `group_id`
- `snapshot` with `prompt_id_self`
- `snapshot` with `group_id_self`
- `snapshot` with `macro_region`
- `snapshot` with `macro_metric`
- `snapshot` with macro `group_id` expansion
- `snapshot` with `full_symbols`
- `snapshot` with `filter`
- market-environment snapshot cases without `symbol`
- `multi_kline` with minute `time_period`
- `single_tick` with one symbol
- `series` with `market_code`
- `series` with `prompt_id_self`
- `series` with `chain_id`
- `series` with `macro_metric`
- `relation_list` with `market_code`
- `relation_list` with `prompt_id`
- `relation_list` with `block_id`
- `relation_list` with `group_id`

Use the request templates first when you want a known-good pattern. Use `references/scenarios.md` to pick the closest scenario family and adjust the JSON for the user request.

## Notes from local cases

- `snapshot` supports additional symbol types beyond the first-pass quote set, including `ths_code`, `group_id`, `prompt_id_self`, `group_id_self`, `macro_region`, and `macro_metric`.
- `snapshot` can return `data.symbol_list` when `full_symbols=true`; this is the full resolved symbol universe, while `data.data` is still paged.
- The local templates and scenario notes show that historical requests can also involve `prompt_id_self`, `chain_id`, and macro-style requests without `symbol`.
- The basic quote templates show how to convert direct symbol requests into `code_list` requests for `multi_kline` and `single_tick`.
- When the user asks for minute quote data or tick-by-tick trade data, prefer this skill and its basic quote endpoints over older indicator-only flows.
- Some advanced metrics require special attrs such as `event_id`, `match_code`, or `tech_param`.
- Some test cases use custom headers like `Market-Level` or `X-Log-Detail`; treat these as case-specific debugging or environment aids, not default request requirements.
- For live endpoint testing, the selected scene auth header is not optional.
- Only sandbox reads auth from an environment variable by default. B-side and C-side auth must come from the caller request or conversation context.
- For B-side, caller-provided auth must match the application behind the endpoint: `index-api` for indicator and relation endpoints, `quoteag` for quote endpoints.
- Only require both B-side apikeys if one user request calls both endpoint families.
- Never write real Cookie or apikey values into repository docs, templates, or durable files.
