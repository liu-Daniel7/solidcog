/* Run with a locally installed Playwright; all writes/model calls are mocked. */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs/promises');
const { chromium } = require('playwright');

(async function () {
    const root = path.resolve(__dirname, '..');
    const server = spawn(process.env.SOLIDCOG_PYTHON || path.join(root, '.venv/bin/python'), [path.join(__dirname, 'workbench_browser_server.py')], { cwd: root });
    let browser;
    const errors = [];
    server.stderr.on('data', data => { if (data.toString().includes('Traceback')) errors.push(data.toString()); });
    try {
        const url = await new Promise((resolve, reject) => {
            let output = '';
            const timer = setTimeout(() => reject(new Error('Fixture server did not start')), 15000);
            server.stdout.on('data', data => {
                output += data.toString();
                const match = output.match(/QA_URL=(http:\/\/\S+)/);
                if (match) { clearTimeout(timer); resolve(match[1]); }
            });
            server.on('error', reject);
            server.on('exit', code => { clearTimeout(timer); reject(new Error(`Fixture server exited ${code}`)); });
        });
        browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) });
        const page = await browser.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/local-model/status', route => route.fulfill({ json: { state: 'mineru_ready', current_mode: 'mineru' } }));
        const screenshots = process.env.SOLIDCOG_SCREENSHOTS;
        if (screenshots) await fs.mkdir(screenshots, { recursive: true });
        for (const [width, height] of [[1920,1080], [1440,1000], [1440,600], [1024,768], [768,1024], [390,844], [320,740]]) {
            await page.setViewportSize({ width, height });
            await page.goto(`${url}/home`);
            await page.locator('.review-drawing').first().waitFor();
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Overflow at ${width}`);
            if (screenshots) await page.screenshot({ path: path.join(screenshots, `workbench-${width}${height === 600 ? '-short' : ''}.png`), fullPage: true });
            await page.locator('#model-settings summary').click();
            const settingBounds = await page.locator('.settings-card').boundingBox();
            assert(settingBounds.x >= 0 && settingBounds.x + settingBounds.width <= width, `Settings outside ${width}`);
            await page.keyboard.press('Escape');
        }
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.goto(`${url}/home`);
        assert.equal(await page.locator('#upload-btn').isDisabled(), true);
        await page.locator('#select-drawing-btn').click();
        await page.locator('.drawing-item').first().waitFor();
        assert.equal(await page.locator('.drawing-item').count(), 4);
        await page.locator('#close-drawing-modal').focus();
        await page.keyboard.press('Shift+Tab');
        assert.equal(await page.locator('.drawing-item').last().evaluate(e => e === document.activeElement), true);
        await page.keyboard.press('Tab');
        assert.equal(await page.locator('#close-drawing-modal').evaluate(e => e === document.activeElement), true);
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('#select-drawing-btn').evaluate(e => e === document.activeElement), true);
        // Selection doesn't pollute chat; search retains context outside its result page.
        await page.locator('.review-drawing').first().click();
        const selectedName = await page.locator('#selected-drawing-name').textContent();
        assert.equal(await page.locator('.message').count(), 0);
        await page.goto(`${url}/search?keyword=not-a-matching-drawing`);
        await page.waitForFunction(name => document.getElementById('selected-drawing-name').textContent === name, selectedName);
        await page.locator('#select-drawing-btn').click();
        await page.locator('.drawing-item').first().waitFor();
        assert.equal(await page.locator('.drawing-item').count(), 4, 'Picker must show the whole library on search pages');
        await page.keyboard.press('Escape');
        await page.goto(`${url}/home`);
        await page.route('**/chat-with-drawing', async route => {
            assert.equal(route.request().postDataJSON().drawing_id, 1);
            await route.fulfill({ json: { success: true, answer: '材料为45钢，关键尺寸请对照原图复核。' } });
        });
        await page.locator('[data-prompt]').last().click();
        await page.locator('#send-btn').click();
        await page.locator('.bot-message').waitFor();
        assert.equal(await page.locator('#assistant-welcome').isVisible(), false);
        await page.reload();
        assert.equal(await page.locator('.message').count(), 2, 'Conversation should survive reload');
        // Check fixed-size upload actions during long filenames and partial failures.
        await page.route('**/upload-drawing-async', route => route.fulfill({ json: { success: true, job: { job_id: 'qa-job' } } }));
        let polls = 0;
        await page.route('**/upload-jobs/qa-job', route => route.fulfill({ json: ++polls === 1
            ? { status: 'processing', completed: 0, total: 2, progress: 0, current_file: '超长图纸名称_'.repeat(30) + '.pdf' }
            : { status: 'partial', completed: 2, total: 2, progress: 100, results: [{ id: 1, filename: selectedName }], errors: [{ filename: '失败.pdf', error: '识别服务暂不可用' }] }
        }));
        await page.locator('#files').setInputFiles([{ name: '图纸.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') }, { name: '失败.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 test') }]);
        await page.locator('#upload-btn').click();
        await page.waitForFunction(() => document.getElementById('upload-progress').textContent.includes('超长图纸名称'));
        const bounds = await page.locator('#upload-btn').boundingBox();
        assert(bounds.width >= 116, 'Upload action must keep a usable width');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await page.waitForFunction(() => document.getElementById('upload-progress').textContent.includes('识别服务暂不可用'));
        await page.waitForFunction(() => !document.getElementById('files').disabled);
        assert.equal(await page.locator('.message').count(), 2, 'Library refresh must preserve chat');
        await page.locator('#files').setInputFiles({ name: 'unsupported.txt', mimeType: 'text/plain', buffer: Buffer.from('test') });
        assert.equal(await page.locator('#upload-btn').isDisabled(), true);
        assert.match(await page.locator('#upload-progress').textContent(), /不支持/);
        // Upload failure keeps the selection and re-enables retry.
        await page.route('**/upload-drawing-async', route => route.fulfill({ status: 502, json: { detail: '识别服务未连接' } }));
        await page.locator('#files').setInputFiles({ name: 'retry.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF test') });
        await page.locator('#upload-btn').click();
        await page.waitForFunction(() => document.getElementById('upload-progress').textContent.includes('识别服务未连接'));
        assert.equal(await page.locator('#upload-btn').isEnabled(), true);
        assert.equal(await page.locator('#upload-button-label').textContent(), '重新识别');
        // Local/cloud choice is explicit and retained on navigation.
        await page.locator('#model-settings summary').click();
        await page.locator('label[for="ocr-qwen"]').click();
        assert.match(await page.locator('#backend-note').textContent(), /发送至 Qwen/);
        await page.reload();
        assert.equal(await page.locator('#upload-ocr-backend').inputValue(), 'qwen');
        // More actions remain inside the screen and OCR pages reflow as well.
        await page.locator('.file-menu summary').last().click();
        const menuBounds = await page.locator('.file-menu[open] .file-menu-items').boundingBox();
        assert(menuBounds.x >= 0 && menuBounds.x + menuBounds.width <= 1440);
        await page.locator('.file-menu summary').first().click();
        const downloadPromise = page.waitForEvent('download');
        await page.locator('.file-menu[open] a[href^="/export-ocr/"]').click();
        const download = await downloadPromise;
        assert.equal(download.suggestedFilename(), `${selectedName}.txt`, 'Export should preserve the Chinese filename');
        for (const width of [1440, 390, 320]) {
            await page.setViewportSize({ width, height: 900 });
            await page.goto(`${url}/view-ocr/2`);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `OCR overflow at ${width}`);
            if (screenshots) await page.screenshot({ path: path.join(screenshots, `ocr-${width}.png`), fullPage: true });
        }
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.goto(`${url}/home`);
        assert.equal(await page.locator('.loading').count(), 0);
        assert.deepEqual(errors, []);
        console.log('PASS: 7 viewport configurations; picker keyboard focus; search context; chat persistence; upload progress, partial failure, validation and retry; backend choice; menus; Chinese filename export; OCR reflow; reduced motion; no page errors.');
    } finally {
        if (browser) await browser.close();
        server.kill('SIGTERM');
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
