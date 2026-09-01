import { test, expect, type Locator, type Page, type Response } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const RESPONSIVE_WIDTHS = [390, 719, 720, 768, 1219] as const;
const EXPOSURE_WIDTHS = [390, 719, 720, 1049, 1219, 1360, 1440] as const;

async function openLatestDetail(page: Page): Promise<Response> {
  await page.goto('/?e2e=latest');
  const quotesResponse = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/quotes') && url.searchParams.get('securityType') === 'stock';
  });
  await page.locator('a.theme-card').first().click();
  const response = await quotesResponse;
  await page.locator('.stock-table').waitFor({ state: 'visible' });
  await expect(page.locator('.stock-record').first()).toBeVisible();
  return response;
}

async function activateSecurityType(page: Page, type: 'stock' | 'etf'): Promise<Locator> {
  const tab = page.getByRole('tab', { name: type === 'stock' ? 'Stock' : 'ETF', exact: true });
  await expect(tab).toBeVisible();
  if (await tab.getAttribute('aria-selected') !== 'true') await tab.click();
  const table = page.locator('.stock-table');
  await expect(table).toBeVisible();
  await expect(table.locator('.stock-record').first()).toBeVisible();
  return table;
}

async function securityOrder(page: Page): Promise<string[]> {
  return page.locator('.stock-record .stock-symbol-link').evaluateAll(links => (
    links.map(link => link.getAttribute('href') || link.textContent?.trim() || '')
  ));
}

async function expectNoLedgerOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => {
    const root = document.querySelector<HTMLElement>('#quote-table-root');
    const frame = document.querySelector<HTMLElement>('.stock-table-frame');
    const table = document.querySelector<HTMLElement>('.stock-table');
    if (!root || !frame || !table) throw new Error('Expected the securities ledger to be rendered.');
    return {
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      root: root.scrollWidth - root.clientWidth,
      frame: frame.scrollWidth - frame.clientWidth,
      table: table.scrollWidth - table.clientWidth,
    };
  });
  expect(overflow.document).toBeLessThanOrEqual(0);
  expect(overflow.root).toBeLessThanOrEqual(0);
  expect(overflow.frame).toBeLessThanOrEqual(0);
  expect(overflow.table).toBeLessThanOrEqual(0);
}

test('detail analytics and exposure scatter use the responsive layout without overflow', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'This test sets every required exposure viewport explicitly.');
  await page.setViewportSize({ width: 1219, height: 1314 });
  await openLatestDetail(page);
  await page.locator('#exposure-scatter-plot svg').waitFor({ state: 'visible' });

  for (const width of EXPOSURE_WIDTHS) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.waitForTimeout(250);
    const [performance, exposure, plot] = await Promise.all([
      page.locator('.performance-section').boundingBox(),
      page.locator('.exposure-section').boundingBox(),
      page.locator('#exposure-scatter-plot').boundingBox(),
    ]);
    expect(performance).not.toBeNull();
    expect(exposure).not.toBeNull();
    expect(plot).not.toBeNull();

    if (width >= 1360) {
      expect(Math.abs(performance!.y - exposure!.y)).toBeLessThanOrEqual(2);
      expect(exposure!.x).toBeGreaterThanOrEqual(performance!.x + performance!.width - 2);
      expect(plot!.height).toBeGreaterThanOrEqual(240);
      expect(plot!.width).toBeGreaterThanOrEqual(590);
    } else {
      expect(exposure!.y).toBeGreaterThanOrEqual(performance!.y + performance!.height - 2);
      expect(plot!.height).toBeGreaterThanOrEqual(width < 720 ? 280 : 260);
      expect(plot!.width).toBeGreaterThanOrEqual(width < 720 ? width - 80 : width * .84);
    }

    const overflow = await page.evaluate(() => {
      const exposureSection = document.querySelector<HTMLElement>('.exposure-section');
      const plotElement = document.querySelector<HTMLElement>('#exposure-scatter-plot');
      return {
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        section: (exposureSection?.scrollWidth || 0) - (exposureSection?.clientWidth || 0),
        plot: (plotElement?.scrollWidth || 0) - (plotElement?.clientWidth || 0),
      };
    });
    expect(overflow.document).toBeLessThanOrEqual(0);
    expect(overflow.section).toBeLessThanOrEqual(0);
    expect(overflow.plot).toBeLessThanOrEqual(0);
  }
});

test('exposure scatter keeps fixed position encodings at every width', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'This test captures both desktop and mobile chart options explicitly.');
  await page.addInitScript(() => {
    const state = window as typeof window & {
      __exposureOptions?: Array<{ option: Record<string, any>; opts?: Record<string, any> }>;
      ThsDataVStandardChart?: unknown;
    };
    state.__exposureOptions = [];
    state.ThsDataVStandardChart = {
      init() {
        const handlers = new Map<string, () => void>();
        const engine = { resize() {}, setOption() {} };
        return {
          on(name: string, handler: () => void) { handlers.set(name, handler); },
          play(payload: { option: Record<string, any>; opts?: Record<string, any> }) {
            state.__exposureOptions!.push(payload);
            handlers.get('dv:afterinit')?.();
          },
          getECharts() { return engine; },
          destroy() {},
        };
      },
    };
  });
  await page.setViewportSize({ width: 1219, height: 1000 });
  await openLatestDetail(page);
  await expect(page.locator('[data-exposure-summary] li')).toHaveCount(8);

  const optionAt = async (width: number) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.waitForTimeout(250);
    return page.evaluate(() => {
      const entries = (window as typeof window & {
        __exposureOptions?: Array<{ option: Record<string, any>; opts?: Record<string, any> }>;
      }).__exposureOptions || [];
      return entries.at(-1);
    });
  };

  const desktop = await optionAt(1219);
  const mobile = await optionAt(390);
  for (const payload of [desktop, mobile]) {
    expect(payload).toBeTruthy();
    const option = payload!.option;
    expect(option.xAxis).toMatchObject({ type: 'value', min: 1, max: 5, interval: 1 });
    expect(option.yAxis.min).toBe(0);
    expect(option.yAxis.max / option.yAxis.interval).toBeLessThanOrEqual(4);
    expect(option.series[0]).toMatchObject({ id: 'exposure-points', type: 'scatter', symbolSize: 6, silent: true });
    expect(typeof option.series[0].symbolSize).toBe('number');
    expect(option.series[0].data).toHaveLength(8);
    expect(option.series[0].data.every((point: { value?: unknown[] }) => point.value?.length === 2)).toBeTruthy();
    expect(option.graphic.filter((item: { type?: string }) => item.type === 'text')).toHaveLength(8);
    expect(payload!.opts?.replaceMerge).toEqual(expect.arrayContaining(['graphic', 'series']));
  }
  expect(desktop!.option.series[0].symbolSize).toBe(mobile!.option.series[0].symbolSize);
});

test('fallback scatter keeps every label visible and supports pointer, touch-sized, and keyboard interaction', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'This test sets the required mobile and desktop viewports explicitly.');
  await page.route('**/standard-chart/**', route => route.abort());
  await page.route(/\/api\/themes\/[^/]+\/exposure-map(?:\?.*)?$/, async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('securityType') !== 'stock') return route.continue();
    await route.fulfill({
      contentType: 'application/json',
      json: {
        points: [
          { symbol: 'META', marketCode: '185:META', exposure: 3.3, marketValue: 1458.04 },
          { symbol: 'AMZN', marketCode: '185:AMZN', exposure: 4.0, marketValue: 1900 },
          { symbol: 'GOOG', marketCode: '185:GOOG', exposure: 4.0, marketValue: 1920 },
          { symbol: 'GOOGL', marketCode: '185:GOOGL', exposure: 4.0, marketValue: 1940 },
          { symbol: 'MA', marketCode: '169:MA', exposure: 4.0, marketValue: 1960 },
          { symbol: 'V', marketCode: '169:V', exposure: 4.0, marketValue: 1980 },
          { symbol: 'TSLA', marketCode: '185:TSLA', exposure: 4.0, marketValue: 2000 },
          { symbol: 'MSFT', marketCode: '185:MSFT', exposure: 4.8, marketValue: 3766.9 },
        ],
      },
    });
  });
  await page.setViewportSize({ width: 1219, height: 1000 });
  await openLatestDetail(page);
  const plot = page.locator('#exposure-scatter-plot');
  await expect(plot.locator('.exposure-scatter-svg')).toBeVisible();
  await expect(page.locator('[data-exposure-summary] li')).toHaveCount(8);

  for (const width of [390, 1049, 1219]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await page.waitForTimeout(250);
    const markers = plot.locator('[data-exposure-marker]');
    const labels = plot.locator('[data-exposure-label]');
    await expect(markers).toHaveCount(8);
    await expect(labels).toHaveCount(8);
    expect(await markers.evaluateAll(elements => [...new Set(elements.map(element => element.getAttribute('r')))])).toEqual(['3']);
    expect(await labels.allTextContents()).toEqual(expect.arrayContaining(['META', 'AMZN', 'GOOG', 'GOOGL', 'MA', 'V', 'TSLA', 'MSFT']));
    const overlaps = await labels.evaluateAll(elements => {
      const boxes = elements.map(element => element.getBoundingClientRect());
      const collisions: Array<[number, number]> = [];
      for (let left = 0; left < boxes.length; left += 1) {
        for (let right = left + 1; right < boxes.length; right += 1) {
          const a = boxes[left];
          const b = boxes[right];
          if (a.x < b.right && a.right > b.x && a.y < b.bottom && a.bottom > b.y) collisions.push([left, right]);
        }
      }
      return collisions;
    });
    expect(overlaps).toEqual([]);
    const overflow = await plot.evaluate(element => element.scrollWidth - element.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  }

  await expect(plot.locator('.exposure-scatter-axis-text')).toHaveText(['1', '2', '3', '4', '5']);
  expect(await plot.locator('.exposure-scatter-y-text').count()).toBeLessThanOrEqual(5);
  const tooltip = plot.locator('[data-exposure-tooltip]');
  const firstMarker = plot.locator('[data-exposure-marker]').first();
  const [plotBox, markerBox] = await Promise.all([plot.boundingBox(), firstMarker.boundingBox()]);
  expect(plotBox).not.toBeNull();
  expect(markerBox).not.toBeNull();
  await page.mouse.click(markerBox!.x + markerBox!.width / 2 + 18, markerBox!.y + markerBox!.height / 2);
  await expect(tooltip).toBeVisible();
  await expect(tooltip).toHaveClass(/is-pinned/);
  await expect(tooltip).toContainText('META');
  await expect(tooltip).toContainText('3.3/5');
  await page.locator('h1').click();
  await expect(tooltip).toBeHidden();

  await plot.focus();
  await plot.press('ArrowRight');
  await expect(page.locator('#exposure-scatter-status')).toContainText('TSLA');
  await expect(tooltip).toContainText('TSLA');
  await plot.press('Enter');
  await expect(tooltip).toHaveClass(/is-pinned/);
  await plot.press('Escape');
  await expect(tooltip).toBeHidden();
  await expect(plot).toBeFocused();

  await page.getByRole('tab', { name: 'ETF', exact: true }).click();
  await expect(plot.locator('.exposure-scatter-svg')).toBeVisible();
  await expect(plot.locator('[data-exposure-marker]')).toHaveCount(5);
  await expect(plot.locator('[data-exposure-label]')).toHaveText(['BCHP', 'GXPD', 'TMGN', 'GXPC', 'AIHY']);
  await expect(page.locator('[data-exposure-summary] li')).toHaveCount(5);
  await expect(page.locator('[data-exposure-summary]')).toContainText('AUM:');
});

test('security-row hover never paints a shaded block', async ({ page, isMobile }) => {
  test.skip(isMobile, 'Hover is a desktop interaction.');
  await openLatestDetail(page);
  const row = page.locator('.stock-data-row').first();
  const symbol = row.locator('.stock-symbol-cell');
  const surface = (locator: Locator) => locator.evaluate(element => {
    const style = getComputedStyle(element);
    return {
      backgroundColor: style.backgroundColor,
      backgroundImage: style.backgroundImage,
      boxShadow: style.boxShadow,
      filter: style.filter,
      opacity: style.opacity,
    };
  });
  const before = await surface(row);
  const symbolBefore = await surface(symbol);
  await row.hover();
  expect(await surface(row)).toEqual(before);
  expect(await surface(symbol)).toEqual(symbolBefore);
});

test('API rationale is rendered once, in full, without disclosure or clamping', async ({ page }) => {
  const response = await openLatestDetail(page);
  expect(response.ok()).toBeTruthy();
  const body = await response.json() as {
    rows?: Array<{ rationale?: string | { en?: string; zh?: string } | null }>;
  };
  const localizedRationales = body.rows?.map(row => typeof row.rationale === 'string'
    ? row.rationale.trim()
    : (row.rationale?.en || row.rationale?.zh || '').trim()) || [];
  const expectedIndex = localizedRationales.findIndex(Boolean);
  const expectedRationale = localizedRationales[expectedIndex];
  expect(expectedIndex).toBeGreaterThanOrEqual(0);
  expect(expectedRationale).toBeTruthy();

  const rendered = page.locator('.stock-record').nth(expectedIndex).locator('.stock-rationale-text');
  await expect(rendered).toHaveCount(1);
  await expect(rendered).toHaveText(expectedRationale!);

  const visibility = await rendered.evaluate(element => {
    const style = getComputedStyle(element);
    return {
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflow: style.overflow,
      overflowY: style.overflowY,
      webkitLineClamp: style.webkitLineClamp,
    };
  });
  expect(visibility.scrollHeight).toBeLessThanOrEqual(visibility.clientHeight + 1);
  expect(visibility.webkitLineClamp).toBe('none');
  expect(visibility.overflow).not.toBe('hidden');
  expect(visibility.overflowY).not.toBe('hidden');

  await expect(page.locator('.description-toggle, .stock-description-row, [data-description], .line-clamp-2')).toHaveCount(0);
});

test('stock and ETF ledgers reflow at the 720px breakpoint without local overflow', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'This test sets each required viewport explicitly.');
  await page.setViewportSize({ width: 1219, height: 1314 });
  await openLatestDetail(page);

  for (const type of ['stock', 'etf'] as const) {
    const table = await activateSecurityType(page, type);
    for (const width of RESPONSIVE_WIDTHS) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await expectNoLedgerOverflow(page);

      const mobileSort = page.locator('.stock-mobile-sort');
      const tableHead = table.locator('.stock-table-head');
      const firstRecord = table.locator('.stock-record').first();
      const dataRow = firstRecord.locator(':scope > .stock-data-row');
      const rationaleRow = firstRecord.locator(':scope > .stock-rationale-row');
      const metrics = dataRow.locator('.stock-metric-cell');
      await expect(dataRow).toBeVisible();
      await expect(rationaleRow).toBeVisible();
      await expect(metrics).toHaveCount(3);

      const [dataBox, rationaleBox] = await Promise.all([dataRow.boundingBox(), rationaleRow.boundingBox()]);
      expect(dataBox).not.toBeNull();
      expect(rationaleBox).not.toBeNull();
      expect(rationaleBox!.y).toBeGreaterThanOrEqual(dataBox!.y + dataBox!.height - 1);

      if (width < 720) {
        await expect(mobileSort).toBeVisible();
        await expect(tableHead).toBeHidden();
        const [rootBox, recordBox, linkBox, sortBox] = await Promise.all([
          page.locator('#quote-table-root').boundingBox(),
          firstRecord.boundingBox(),
          firstRecord.locator('.stock-symbol-link').boundingBox(),
          mobileSort.locator('[data-sort-location="mobile"]').first().boundingBox(),
        ]);
        expect(rootBox).not.toBeNull();
        expect(recordBox).not.toBeNull();
        expect(recordBox!.x).toBeGreaterThan(rootBox!.x);
        expect(recordBox!.x + recordBox!.width).toBeLessThan(rootBox!.x + rootBox!.width);
        expect(linkBox?.height || 0).toBeGreaterThanOrEqual(44);
        expect(sortBox?.height || 0).toBeGreaterThanOrEqual(44);

        const metricBoxes = await metrics.evaluateAll(elements => elements.map(element => {
          const box = element.getBoundingClientRect();
          return { x: box.x, y: box.y };
        }));
        expect(Math.max(...metricBoxes.map(box => box.y)) - Math.min(...metricBoxes.map(box => box.y))).toBeLessThanOrEqual(2);
        expect(metricBoxes[0].x).toBeLessThan(metricBoxes[1].x);
        expect(metricBoxes[1].x).toBeLessThan(metricBoxes[2].x);
        await expect(metrics.locator('.stock-metric-label')).toHaveCount(3);
        for (const label of await metrics.locator('.stock-metric-label').all()) await expect(label).toBeVisible();
      } else {
        await expect(mobileSort).toBeHidden();
        await expect(tableHead).toBeVisible();
      }
    }
  }
});

test('mobile and desktop sort controls produce the same row order', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'This test sets both responsive viewports explicitly.');
  await page.route(/\/api\/themes\/[^/]+\/quotes(?:\?.*)?$/, async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('securityType') !== 'stock') return route.continue();
    const response = await route.fetch();
    const body = await response.json() as { rows?: Array<Record<string, unknown>> };
    const rows = (body.rows || []).map((row, index) => ({ ...row, last: index + 1 }));
    await route.fulfill({ response, json: { ...body, rows } });
  });

  await page.setViewportSize({ width: 768, height: 1000 });
  await openLatestDetail(page);
  const initialOrder = await securityOrder(page);
  expect(initialOrder.length).toBeGreaterThan(1);
  const desktopSort = page.locator('[data-sort="last"][data-sort-location="desktop"]');
  await expect(desktopSort).toBeVisible();
  await desktopSort.click();
  await expect(desktopSort).toBeFocused();
  await expect(page.locator('[role="columnheader"][aria-sort="descending"]')).toBeVisible();
  const desktopDescending = await securityOrder(page);
  expect(desktopDescending).toEqual([...initialOrder].reverse());

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.locator('.stock-record').first()).toBeVisible();
  const mobileSort = page.locator('[data-sort="last"][data-sort-location="mobile"]');
  await expect(mobileSort).toBeVisible();
  await mobileSort.click();
  await expect(mobileSort).toBeFocused();
  const mobileDescending = await securityOrder(page);
  expect(mobileDescending).toEqual(desktopDescending);
  await mobileSort.click();
  expect(await securityOrder(page)).toEqual(initialOrder);
});

test('security identity is the only record link and rationale interaction never navigates', async ({ page }) => {
  await page.route('https://value-investment.emery-xu1.workers.dev/**', route => (
    route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Investment analysis</title>' })
  ));
  await openLatestDetail(page);
  const originalUrl = page.url();
  const firstRecord = page.locator('.stock-record').first();
  const link = firstRecord.locator('a.stock-symbol-link');
  const rationale = firstRecord.locator('.stock-rationale-text');
  await expect(link).toHaveCount(1);
  await expect(firstRecord.locator('a')).toHaveCount(1);
  await expect(link).toHaveAttribute('href', /\/value-opportunities\//);
  await expect(rationale).toBeVisible();

  await rationale.click();
  await expect(page).toHaveURL(originalUrl);

  await link.focus();
  await expect(link).toBeFocused();
  const focusIndicator = await link.evaluate(element => {
    const style = getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
      boxShadow: style.boxShadow,
    };
  });
  expect(
    (focusIndicator.outlineStyle !== 'none' && focusIndicator.outlineWidth > 0)
      || focusIndicator.boxShadow !== 'none',
  ).toBeTruthy();
  const href = await link.getAttribute('href');
  expect(href).toBeTruthy();
  await link.press('Enter');
  await expect(page).toHaveURL(href!);

  await page.goBack();
  await expect(page.locator('.stock-record').first()).toBeVisible();
  const mouseLink = page.locator('.stock-record').first().locator('a.stock-symbol-link');
  await mouseLink.click();
  await expect(page).toHaveURL(href!);
});

test('long English, CJK, and unbroken rationales remain fully visible', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name === 'mobile', 'This test sets mobile and desktop viewports explicitly.');
  const seeds = [
    'A long-form legacy rationale remains readable and preserves every word across responsive layouts. ',
    '通胀降温与企业盈利能力的长期关系需要完整展示，不能截断或隐藏。',
    'UnbrokenLegacyRationale0123456789',
  ];
  const samples = seeds.map(seed => seed.repeat(Math.ceil(1200 / seed.length)).slice(0, 1200));
  await page.route(/\/api\/themes\/[^/]+\/quotes(?:\?.*)?$/, async route => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('securityType') !== 'stock') return route.continue();
    const response = await route.fetch();
    const body = await response.json() as { rows?: Array<Record<string, unknown>> };
    const rows = (body.rows || []).map((row, index) => index < samples.length
      ? { ...row, rationale: samples[index] }
      : row);
    await route.fulfill({ response, json: { ...body, rows } });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await openLatestDetail(page);
  const rationales = page.locator('.stock-rationale-text');
  expect(await rationales.count()).toBeGreaterThanOrEqual(samples.length);

  for (const width of [390, 720, 1219]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    await expectNoLedgerOverflow(page);
    for (let index = 0; index < samples.length; index += 1) {
      const rationale = rationales.nth(index);
      await expect(rationale).toHaveText(samples[index]);
      const geometry = await rationale.evaluate(element => {
        const root = element.closest<HTMLElement>('#quote-table-root');
        const box = element.getBoundingClientRect();
        const rootBox = root?.getBoundingClientRect();
        return {
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          right: box.right,
          rootRight: rootBox?.right || 0,
          webkitLineClamp: getComputedStyle(element).webkitLineClamp,
        };
      });
      expect(geometry.scrollHeight).toBeLessThanOrEqual(geometry.clientHeight + 1);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
      expect(geometry.right).toBeLessThanOrEqual(geometry.rootRight + 1);
      expect(geometry.webkitLineClamp).toBe('none');
    }
  }
});

test('detail ledger keeps accessible reading order and metric labels', async ({ page }) => {
  await openLatestDetail(page);
  const table = page.locator('.stock-table');
  await expect(table).toHaveAttribute('role', 'table');
  await expect(table).toHaveAttribute('aria-label', /Stock quotes/i);
  const firstRecord = table.locator('.stock-record').first();
  await expect(firstRecord).toHaveAttribute('role', 'rowgroup');
  const readingOrder = await firstRecord.evaluate(record => {
    const summary = record.querySelector('.stock-data-row');
    const rationale = record.querySelector('.stock-rationale-row');
    return Boolean(summary && rationale && (summary.compareDocumentPosition(rationale) & Node.DOCUMENT_POSITION_FOLLOWING));
  });
  expect(readingOrder).toBeTruthy();
  const labels = firstRecord.locator('.stock-metric-label');
  await expect(labels).toHaveCount(3);
  const labelTexts = (await labels.allTextContents()).map(text => text.trim());
  expect(labelTexts.every(Boolean)).toBeTruthy();
  await expect(page.locator('#quote-table-root')).not.toHaveAttribute('aria-live');
  await expect(page.locator('#quote-table-status')).toHaveAttribute('role', 'status');
  if ((page.viewportSize()?.width || 0) < 720) {
    for (const label of await labels.all()) await expect(label).toBeVisible();
    await expect(labels.nth(1)).toHaveText(/^Since [A-Z][a-z]{2} \d{1,2}$/);
    await expect(firstRecord.locator('.stock-metric-cell').nth(1)).toHaveAttribute('aria-label', /% Chg since [A-Z][a-z]{2} \d{1,2}, \d{4}:/);
  }

  const visibleSort = page.locator((page.viewportSize()?.width || 0) < 720
    ? '[data-sort="last"][data-sort-location="mobile"]'
    : '[data-sort="last"][data-sort-location="desktop"]');
  await visibleSort.click();
  await expect(visibleSort).toBeFocused();
  await expect(page.locator('#quote-table-status')).toContainText(/securities sorted by Last, descending\./);
});

test('detail has no serious accessibility violations', async ({ page }) => {
  await openLatestDetail(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')).toEqual([]);
});
