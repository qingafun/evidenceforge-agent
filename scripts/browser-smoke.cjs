// Optional browser acceptance: npm install --no-save playwright && npx playwright install chromium
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.EF_BROWSER_CHANNEL ? { channel: process.env.EF_BROWSER_CHANNEL } : {}) });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1080 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  const base = process.env.EF_TEST_URL || 'http://127.0.0.1:8000';
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.locator('#question').fill('如何构建支持持久化、人工审批和评测的 RAG Agent？');
  await page.locator('#start-research').click();
  await page.locator('#approval-panel').waitFor({ state: 'visible', timeout: 20000 });
  await page.locator('#approval-feedback').fill('优先本地部署，明确方案的验证方法。');
  await page.locator('#accept-approval').click();
  await page.waitForFunction(() => document.getElementById('run-status').textContent === '已完成', null, { timeout: 30000 });
  await page.locator('[data-tab="evidence"]').click();
  if (!(await page.locator('#evidence-list').textContent())) {
    throw new Error('Evidence view was empty');
  }
  await page.locator('[data-tab="trace"]').click();
  await page.locator('[data-tab="report"]').click();
  const download = page.waitForEvent('download');
  await page.locator('#export-report').click();
  if (!(await download).suggestedFilename().endsWith('.md')) throw new Error('Markdown export failed');
  fs.mkdirSync(path.join('docs', 'assets'), { recursive: true });
  await page.waitForTimeout(4500);
  await page.screenshot({ path: 'docs/assets/workspace.png', fullPage: false });
  await page.locator('[data-view="knowledge"]').click();
  await page.locator('#document-title').fill('Browser acceptance fixture');
  await page.locator('#document-content').fill('Browser fixture for checkpoint recovery and evidence retrieval. <script>window.bad=1</script>');
  await page.locator('#add-document').click();
  await page.getByRole('button', { name: '删除文档 Browser acceptance fixture' }).waitFor();
  await page.getByRole('button', { name: '删除文档 Browser acceptance fixture' }).click();
  await page.getByRole('button', { name: '删除文档 Browser acceptance fixture' }).waitFor({ state: 'detached' });
  if (await page.evaluate(() => window.bad)) throw new Error('Untrusted document HTML executed');
  await page.locator('[data-view="memory"]').click();
  await page.locator('#memory-content').fill('Browser fixture: prefer SQLite');
  await page.locator('#add-memory').click();
  await page.getByRole('button', { name: '删除记忆：Browser fixture: prefer SQLite' }).waitFor();
  await page.getByRole('button', { name: '删除记忆：Browser fixture: prefer SQLite' }).click();
  await page.getByRole('button', { name: '删除记忆：Browser fixture: prefer SQLite' }).waitFor({ state: 'detached' });
  await page.locator('[data-view="evaluations"]').click();
  await page.locator('.eval-summary-grid').waitFor();
  await page.screenshot({ path: 'docs/assets/evaluation.png', fullPage: false });
  await page.locator('[data-view="workspace"]').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'docs/assets/mobile.png', fullPage: false });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
  if (overflow) throw new Error('Mobile viewport overflows horizontally');
  await page.locator('#mobile-menu').click();
  await page.locator('[data-view="knowledge"]').click();
  await page.locator('#view-knowledge').waitFor({ state: 'visible' });
  await page.goto(base + '/docs', { waitUntil: 'networkidle' });
  await page.locator('#api-endpoint-count').filter({ hasText: /\d/ }).waitFor();
  await page.locator('#api-search').fill('/approve');
  if (!(await page.locator('#api-endpoints').textContent()).includes('/approve')) throw new Error('Offline API docs failed');
  await browser.close();
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ passed: true, checks: ['approval', 'report', 'evidence', 'trace', 'download', 'document CRUD', 'memory CRUD', 'evaluations', 'mobile navigation', 'offline API docs', 'no page errors'] }));
})().catch(error => { console.error(error); process.exit(1); });
