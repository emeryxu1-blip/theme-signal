import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('archive is cover-led and cards navigate as full links', async ({ page }) => {
  await page.goto('/?e2e=latest');
  await expect(page.locator('h1')).toContainText('moving markets');
  const cards = page.locator('a.theme-card');
  await expect(cards.first()).toBeVisible();
  await expect(cards.first()).toHaveAttribute('href', /\/themes\//);
  await expect(cards.first()).not.toContainText(/stocks|ETFs|View theme/i);
  await expect(cards.first().locator('.theme-card-open')).toHaveCount(0);
  await cards.first().click();
  await expect(page).toHaveURL(/\/themes\/[^/]+$/);
  await expect(page.locator('h1')).toBeVisible();
});

test('archive exposes one clear navigation action per destination', async ({ page }) => {
  await page.goto('/?e2e=latest');
  await expect(page.locator('nav .brand')).toHaveCount(1);
  await expect(page.locator('nav a')).toHaveCount(1);
  await expect(page.getByText('Browse archive', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Themes', { exact: true })).toHaveCount(0);
  await expect(page.locator('.archive-footer')).toHaveText('Educational research, not investment advice.');
});

test('detail keeps one archive link and hides the source link', async ({ page }) => {
  await page.goto('/?e2e=latest');
  await page.locator('a.theme-card').first().click();
  await expect(page.locator('.h5-nav, .h5-back')).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'All themes' })).toHaveCount(1);
  await expect(page.getByRole('link', { name: /Original source/ })).toHaveCount(0);
  await expect(page.getByText('Read Full Analysis', { exact: true })).toHaveCount(0);
});

test('archive metadata and crawler endpoints are present', async ({ page, request }) => {
  await page.goto('/?e2e=latest');
  await expect(page).toHaveTitle(/ThemeSignal/);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /https?:\/\//);
  await expect(page.locator('script[type="application/ld+json"]')).toHaveCount(1);
  const robots = await request.get('/robots.txt');
  expect(robots.ok()).toBeTruthy();
  expect(await robots.text()).toContain('Sitemap:');
  const sitemap = await request.get('/sitemap.xml');
  expect(sitemap.ok()).toBeTruthy();
  expect(await sitemap.text()).toContain('<urlset');
});

test('archive has no serious accessibility violations', async ({ page }) => {
  await page.goto('/?e2e=latest');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.filter(item => item.impact === 'critical' || item.impact === 'serious')).toEqual([]);
});
