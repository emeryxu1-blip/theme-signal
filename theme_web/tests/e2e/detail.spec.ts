import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function openLatestDetail(page) {
  await page.goto('/?e2e=latest');
  await page.locator('a.theme-card').first().click();
  await page.locator('.stock-table').waitFor({ state: 'visible' });
}

test('detail analytics use the responsive layout without page overflow', async ({ page }) => {
  await openLatestDetail(page);
  const performance = await page.locator('.performance-section').boundingBox();
  const exposure = await page.locator('.exposure-section').boundingBox();
  const viewport = page.viewportSize();
  expect(performance).not.toBeNull();
  expect(exposure).not.toBeNull();

  if ((viewport?.width || 0) >= 960) {
    expect(Math.abs(performance!.y - exposure!.y)).toBeLessThanOrEqual(2);
    expect(exposure!.x).toBeGreaterThanOrEqual(performance!.x + performance!.width - 2);
  } else {
    expect(exposure!.y).toBeGreaterThanOrEqual(performance!.y + performance!.height - 2);
  }

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('security-row hover never paints a shaded block', async ({ page, isMobile }) => {
  test.skip(isMobile, 'Hover is a desktop interaction.');
  await openLatestDetail(page);
  const row = page.locator('.stock-data-row').first();
  const symbol = row.locator('.stock-symbol-cell');
  const surface = locator => locator.evaluate(element => {
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

test('security rationale disclosure is connected and keyboard accessible', async ({ page }) => {
  await openLatestDetail(page);
  const toggle = page.locator('.description-toggle').first();
  const targetId = await toggle.getAttribute('aria-controls');
  expect(targetId).toBeTruthy();
  const target = page.locator(`#${targetId}`);
  await expect(target).toBeHidden();
  await toggle.focus();
  await toggle.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(target).toBeVisible();
  await toggle.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(target).toBeHidden();
});

test('detail has no serious accessibility violations', async ({ page }) => {
  await openLatestDetail(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')).toEqual([]);
});
