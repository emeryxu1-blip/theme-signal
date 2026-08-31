export const SITE_NAME = 'ThemeSignal';
export const DEFAULT_SITE_ORIGIN = 'https://theme-signal-archive.emery-xu1.workers.dev';
export const HOME_TITLE = 'Trending Market Themes & Investment Research | ThemeSignal';
export const HOME_DESCRIPTION = 'Explore current market themes, the companies and funds connected to them, and the research context behind each signal.';

export interface ThemeListRow {
  id: string;
  created_at: string;
  event_date: string | null;
  theme: string;
  source_url: string | null;
  cover_url: string | null;
  stock_count: number;
  etf_count: number;
}

export interface FaqRow {
  question: string;
  answer: string;
}

const HTML_ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, character => HTML_ESCAPES[character]);
}

export function normalizeOrigin(value: string | undefined | null): string {
  try {
    const url = new URL(String(value || DEFAULT_SITE_ORIGIN));
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return DEFAULT_SITE_ORIGIN;
    return url.origin;
  } catch {
    return DEFAULT_SITE_ORIGIN;
  }
}

export function safeHttpUrl(value: unknown, baseOrigin = DEFAULT_SITE_ORIGIN, upgradeInsecure = true): string {
  if (!value) return '';
  try {
    const url = new URL(String(value), baseOrigin);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
    if (upgradeInsecure && normalizeOrigin(baseOrigin).startsWith('https:') && url.protocol === 'http:') {
      url.protocol = 'https:';
    }
    return url.href;
  } catch {
    return '';
  }
}

export function canonicalThemeUrl(origin: string, id: string): string {
  return `${normalizeOrigin(origin)}/themes/${encodeURIComponent(id)}`;
}

export function canonicalHomeUrl(origin: string): string {
  return `${normalizeOrigin(origin)}/`;
}

export function truncate(value: unknown, limit = 160): string {
  const text = String(value ?? '').replace(/\s+/gu, ' ').trim();
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

export function formatDate(value: unknown): string {
  const text = String(value ?? '').trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const date = new Date(`${text}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

export function validDate(value: unknown): string | null {
  const text = String(value ?? '').trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : null;
}

export function localizedText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value.trim() || fallback;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fallback;
  const record = value as Record<string, unknown>;
  return String(record.en || record.zh || fallback).trim();
}

export function faqRows(data: Record<string, any> | null | undefined): FaqRow[] {
  const source = Array.isArray(data?.ThemeFAQ) ? data.ThemeFAQ : [];
  return source.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const question = localizedText(item.question);
    const answer = localizedText(item.answer);
    return question && answer ? [{ question, answer }] : [];
  });
}

export function themeDescription(data: Record<string, any> | null | undefined, row?: Partial<ThemeListRow>): string {
  const theme = String(data?.theme || row?.theme || 'Market theme').trim() || 'Market theme';
  const faq = faqRows(data)[0];
  const context = faq?.answer ? ` ${faq.answer}` : '';
  return truncate(`Research context for ${theme}: explore the market event, related securities, and the evidence behind this theme.${context}`, 160);
}

export function safeJson(value: unknown): string {
  const serialized = JSON.stringify(value) ?? 'null';
  return serialized.replace(/[<>&\u2028\u2029]/gu, character => {
    switch (character) {
      case '<': return '\\u003C';
      case '>': return '\\u003E';
      case '&': return '\\u0026';
      case '\u2028': return '\\u2028';
      case '\u2029': return '\\u2029';
      default: return character;
    }
  });
}

export function titleForTheme(theme: unknown): string {
  const name = truncate(theme || 'Market theme', 80) || 'Market theme';
  return `${name} — Market Theme Signal | ${SITE_NAME}`;
}

export function archiveCardMarkup(row: ThemeListRow, origin: string, index: number): string {
  const href = canonicalThemeUrl(origin, row.id);
  const title = String(row.theme || 'Untitled theme');
  const cover = safeHttpUrl(row.cover_url, origin);
  const date = validDate(row.event_date);
  const featured = index === 0;
  return `<article class="theme-card-wrap${featured ? ' theme-card-wrap-featured' : ''}">
    <a class="theme-card${featured ? ' theme-card-featured' : ''}" href="${escapeHtml(href)}" aria-label="Open ${escapeHtml(title)} theme">
      <div class="theme-card-media${cover ? '' : ' theme-card-media-placeholder'}">${cover ? `<img class="theme-cover" src="${escapeHtml(cover)}" alt="${escapeHtml(title)} market theme cover" width="1600" height="900"${featured ? ' fetchpriority="high"' : ' loading="lazy"'} decoding="async">` : '<span class="theme-cover-placeholder" aria-hidden="true"></span>'}<span class="theme-card-shade" aria-hidden="true"></span></div>
      <div class="theme-card-body">
        <p class="card-date">${date ? `<time datetime="${escapeHtml(date)}">${escapeHtml(formatDate(date))}</time>` : 'Market signal'}</p>
        <h2>${escapeHtml(title)}</h2>
      </div>
    </a>
  </article>`;
}

export function archiveJsonLd(rows: ThemeListRow[], origin: string): Record<string, unknown> {
  const home = canonicalHomeUrl(origin);
  return {
    '@context': 'https://schema.org',
    '@graph': [
      {
        '@type': 'WebSite',
        '@id': `${home}#website`,
        name: SITE_NAME,
        url: home,
        description: HOME_DESCRIPTION,
      },
      {
        '@type': 'CollectionPage',
        '@id': `${home}#collection`,
        name: HOME_TITLE,
        url: home,
        isPartOf: { '@id': `${home}#website` },
        mainEntity: { '@id': `${home}#theme-list` },
      },
      {
        '@type': 'ItemList',
        '@id': `${home}#theme-list`,
        itemListElement: rows.map((row, index) => ({
          '@type': 'ListItem',
          position: index + 1,
          name: row.theme,
          url: canonicalThemeUrl(origin, row.id),
        })),
      },
    ],
  };
}

export function detailJsonLd(data: Record<string, any>, row: ThemeListRow, origin: string): Record<string, unknown> {
  const canonical = canonicalThemeUrl(origin, row.id);
  const home = canonicalHomeUrl(origin);
  const cover = safeHttpUrl(data.cover || row.cover_url, origin);
  const published = validDate(data.date || row.event_date);
  const source = safeHttpUrl(data.url || row.source_url, origin, false);
  const faq = faqRows(data);
  const graph: Record<string, unknown>[] = [
    {
      '@type': 'WebPage',
      '@id': `${canonical}#webpage`,
      name: titleForTheme(data.theme || row.theme),
      url: canonical,
      description: themeDescription(data, row),
      isPartOf: { '@id': `${home}#website` },
      ...(cover ? { image: cover } : {}),
      ...(published ? { datePublished: published, dateModified: published } : {}),
      ...(source ? { isBasedOn: source } : {}),
    },
    {
      '@type': 'BreadcrumbList',
      '@id': `${canonical}#breadcrumbs`,
      itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'All themes', item: home },
        { '@type': 'ListItem', position: 2, name: data.theme || row.theme, item: canonical },
      ],
    },
  ];
  if (faq.length) {
    graph.push({
      '@type': 'FAQPage',
      '@id': `${canonical}#faq`,
      mainEntity: faq.map(item => ({
        '@type': 'Question',
        name: item.question,
        acceptedAnswer: { '@type': 'Answer', text: item.answer },
      })),
    });
  }
  return { '@context': 'https://schema.org', '@graph': graph };
}

export function xmlEscape(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, character => {
    switch (character) {
      case '&': return '&amp;';
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '"': return '&quot;';
      case "'": return '&apos;';
      default: return character;
    }
  });
}
