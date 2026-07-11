#!/usr/bin/env node
/**
 * Prod screenshot / smoke-test script for DSS Control Plane.
 *
 * Requires puppeteer-core and a local Chrome installation:
 *   npm install puppeteer-core
 *
 * Run with your browser session token:
 *   DSS_SESSION_TOKEN="<value of ds_backend_session_token cookie>" \
 *   node scripts/prod_screenshot.js
 *
 * The script sets the session cookie, navigates to /connections and
 * /connections/setup-guide, logs visible entities, and writes screenshots
 * to /tmp/dss-prod-*.png.
 */

const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const PROD_BASE = process.env.DSS_PROD_BASE || '';
const SESSION_TOKEN = (process.env.DSS_SESSION_TOKEN || '').trim();

if (!SESSION_TOKEN) {
  console.error('Error: set DSS_SESSION_TOKEN to your ds_backend_session_token cookie value.');
  process.exit(1);
}

const CHROME_PATH = process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

if (!fs.existsSync(CHROME_PATH)) {
  console.error(`Chrome not found at ${CHROME_PATH}. Set CHROME_PATH or install Chrome.`);
  process.exit(1);
}

async function run() {
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: CHROME_PATH,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });

    // Set the session cookie for the configured production domain
    const urlObj = new URL(PROD_BASE);
    await page.setCookie({
      name: 'ds_backend_session_token',
      value: SESSION_TOKEN,
      domain: urlObj.hostname,
      path: '/',
      httpOnly: true,
      secure: true,
    });

    // Navigate once so subsequent fetches run on the correct origin.
    await page.goto(PROD_BASE, { waitUntil: 'domcontentloaded' });

    // Verify identity card
    console.log('GET /api/auth/identity_card');
    const identityResponse = await page.evaluate(async (base) => {
      const res = await fetch(`${base}/api/auth/identity_card`, {
        headers: { accept: 'application/json' },
        credentials: 'include',
      });
      return { status: res.status, body: await res.json().catch(() => ({})) };
    }, PROD_BASE);
    console.log(`  -> ${identityResponse.status}`);
    const vc = identityResponse.body.identity_vc || {};
    console.log(`  principal_did: ${vc.principal_did || 'n/a'}`);
    console.log(`  tenant_id:     ${vc.tenant_id || 'n/a'}`);
    console.log(`  ledger_id:     ${vc.ledger_id || 'n/a'}`);
    console.log(`  display_name:  ${vc.display_name || vc.principal_display_name || 'n/a'}`);
    console.log(`  verified:      ${vc.verified}`);

    // /connections
    console.log('\nNavigating to /connections ...');
    await page.goto(`${PROD_BASE}/connections`, { waitUntil: 'networkidle2' });
    const connectionsTitle = await page.title();
    console.log(`  title: ${connectionsTitle}`);
    const connectionsPath = await page.evaluate(() => window.location.pathname);
    console.log(`  path:  ${connectionsPath}`);

    const connectionRows = await page.$$eval('.collection-list-row', (rows) =>
      rows.map((r) => ({
        text: r.innerText.trim().replace(/\s+/g, ' '),
        type: r.getAttribute('data-pageable-kind'),
      }))
    );
    console.log(`  connection rows: ${connectionRows.length}`);
    connectionRows.slice(0, 10).forEach((r) => console.log(`    - ${r.text.substring(0, 140)}`));

    const bannerText = await page.evaluate(() => {
      const banner = document.querySelector('.banner.warn');
      return banner ? banner.innerText.trim().replace(/\s+/g, ' ') : '';
    });
    if (bannerText) console.log(`  banner: ${bannerText.substring(0, 200)}`);

    await page.screenshot({ path: '/tmp/dss-prod-connections.png', fullPage: true });
    console.log('  screenshot: /tmp/dss-prod-connections.png');

    // /connections/setup-guide
    console.log('\nNavigating to /connections/setup-guide ...');
    await page.goto(`${PROD_BASE}/connections/setup-guide`, { waitUntil: 'networkidle2' });
    const guideTitle = await page.title();
    console.log(`  title: ${guideTitle}`);
    const guidePath = await page.evaluate(() => window.location.pathname);
    console.log(`  path:  ${guidePath}`);

    const guideStepText = await page.evaluate(() => {
      const visible = document.querySelector('.wizard-step:not([hidden])');
      return visible ? visible.innerText.trim().replace(/\s+/g, ' ') : '';
    });
    if (guideStepText) console.log(`  visible step: ${guideStepText.substring(0, 200)}`);

    await page.screenshot({ path: '/tmp/dss-prod-setup-guide.png', fullPage: true });
    console.log('  screenshot: /tmp/dss-prod-setup-guide.png');

    // /ledgers/chat-demo if ledger visible
    console.log('\nNavigating to /ledgers/chat-demo ...');
    try {
      await page.goto(`${PROD_BASE}/ledgers/chat-demo`, { waitUntil: 'networkidle2', timeout: 10000 });
      const ledgerTitle = await page.title();
      console.log(`  title: ${ledgerTitle}`);
      await page.screenshot({ path: '/tmp/dss-prod-ledger-chat-demo.png', fullPage: true });
      console.log('  screenshot: /tmp/dss-prod-ledger-chat-demo.png');
    } catch (e) {
      console.log(`  failed: ${e.message}`);
    }

  } finally {
    await browser.close();
  }
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
