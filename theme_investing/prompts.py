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
    "You are scoring how exposed each company/ETF is to a specific investment theme. "
    "Judge by real business/revenue exposure, not by ticker or name resemblance. "
    "If you are unsure what a company does, give a low score with low confidence. "
    "For ETFs, penalize leveraged/inverse/complex products unless the event directly supports them."
)

RELEVANCE_USER = """Theme brief:
{brief}

Score each candidate below for exposure to this theme.
Candidates (market_code | name):
{candidates}

Return a JSON array; one object per candidate, in the SAME order, each with:
- "market_code": echo the candidate code exactly.
- "ai_relevance": integer 1-5 (5 = pure-play/direct, 3 = meaningful, 1 = none).
- "exposure_type": one of "direct","enabler","supply_chain","beneficiary","diversified","unclear".
- "confidence": float 0-1.
- "reason": <=15 words."""

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
