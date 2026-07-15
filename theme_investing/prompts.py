"""Prompt templates for the theme-investing workflow LLM stages."""

EVENT_BRIEF_SYS = (
    "You are a senior equity strategist. Given a market theme, the date it became "
    "hot, and a related article, produce a rigorous, investment-grade event brief. "
    "Distinguish real beneficiaries from superficial keyword matches. Be concrete."
)

EVENT_BRIEF_USER = """Theme: {theme}
Date it became hot: {date}
Article title: {title}
Article source: {url}
Article excerpt (may be truncated or empty):
\"\"\"{excerpt}\"\"\"

Return JSON with keys:
- "summary": 2-3 sentences on what happened and why it matters.
- "thesis": the investable thesis in 1-2 sentences.
- "direct_beneficiaries": list of company/product/technology descriptors (not tickers) with the most direct revenue exposure.
- "picks_and_shovels": enablers/suppliers that benefit indirectly.
- "second_order": downstream or adjacent beneficiaries.
- "false_positives": types of names that look related by keyword but are NOT real beneficiaries.
- "keywords": 5-15 short search keywords for this theme."""

RELEVANCE_SYS = (
    "You are a rigorous buy-side analyst scoring each company/ETF's TRUE business exposure "
    "to a specific investment theme. "
    "RULES — follow them strictly:\n"
    "1. Score ONLY on real, quantifiable revenue or earnings exposure. A company that merely "
    "operates in the same broad sector does NOT qualify as a beneficiary.\n"
    "2. Do NOT use ticker symbol, company name, or keyword overlap as evidence of exposure. "
    "A company named 'AI Corp' may score 1; a semiconductor IP firm may score 5.\n"
    "3. Direct beneficiaries (score 4-5): the theme must plausibly drive >5% incremental revenue "
    "or margin expansion for them WITHIN 12 months.\n"
    "4. When you are uncertain what a company actually does, assign ai_relevance=1 and "
    "confidence<=0.3. Never guess high.\n"
    "5. For ETFs: score the weighted-average exposure of their TOP holdings, not the fund name. "
    "Leveraged/inverse ETFs score 1 unless the event directly supports their specific direction.\n"
    "6. Resist recency and popularity bias — a high-profile name that only tangentially touches "
    "the theme should score lower than an obscure pure-play."
)

RELEVANCE_USER = """Theme brief (includes direct_beneficiaries, picks_and_shovels, false_positives):
{brief}

Score each candidate below for DIRECT business/revenue exposure to this theme.
Reject keyword coincidences and broad-sector membership — see false_positives in the brief.
Candidates (market_code | name):
{candidates}

Return a JSON array; one object per candidate, in the SAME order, each with:
- "market_code": echo the candidate code exactly.
- "ai_relevance": integer 1-5 (5 = pure-play direct revenue driver, 4 = meaningful direct,
  3 = clear enabler/supply-chain, 2 = marginal or speculative, 1 = no real exposure).
- "exposure_type": one of "direct","enabler","supply_chain","beneficiary","diversified","unclear".
- "confidence": float 0-1 (use <=0.3 when you are not sure what the company does).
- "reason": <=15 words citing the SPECIFIC product/segment driving exposure, not just industry."""

NARRATIVE_SYS = (
    "You are an equity research analyst writing concise, factual notes. "
    "Ground bullish rationale in the security's actual exposure and the observed "
    "price/volume confirmation provided. No hype, no guarantees, no price targets."
)

NARRATIVE_USER = """Theme: {theme}
Theme thesis: {thesis}
Data as of: {as_of}

For each selected {kind} below, write an intro and a bullish rationale.
Records (JSON):
{records}

Return a JSON array, one object per record in the same order, each with:
- "market_code": echo exactly.
- "intro": 1-2 sentences on what the {kind} is.
- "why_bullish": 1-2 sentences tying it to the theme and citing its exposure and momentum."""

FAQ_SYS = (
    "You write SEO-friendly investor FAQs: clear search-intent questions with concise, "
    "accurate answers. Educational tone, not personalized advice, no return guarantees."
)

FAQ_USER = """Theme: {theme}
Thesis: {thesis}
Top stocks: {stocks}
Top ETFs: {etfs}

Write {n_min}-{n_max} FAQ items covering: what the theme is, why the {date} event matters,
which stocks/ETFs give exposure, key risks/limitations, and how to monitor the theme.
Return a JSON array of objects with keys "question" and "answer"."""
