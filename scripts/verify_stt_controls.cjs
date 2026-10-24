const assert = require('node:assert/strict');
const {chromium} = require(process.env.GAMMA_PLAYWRIGHT_PACKAGE || '/home/neety/.local/lib/python3.13/site-packages/playwright/driver/package');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const context = await browser.newContext();
    const base = process.env.FIXTURE_BASE;
    await context.request.post(base + '/login', {form: {username: 'fixture', password: process.env.FIXTURE_PASSWORD}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(base + '/dashboard/workers');
    await page.waitForFunction(() => document.querySelector('#brain-status').textContent.includes('Interactive:'));
    assert.match(await page.locator('#brain-status').innerText(), /Background:/);
    await page.waitForFunction(() => document.querySelector('#silent-status').textContent.includes('Completed: 1'));
    const pause = await context.request.post(base + '/api/brain/pause');
    assert.equal(pause.status(), 200);
    await page.locator('#silent-enable').click();
    await page.waitForFunction(() => document.querySelector('#silent-status').textContent.startsWith('Enabled'));
    await page.locator('#silent-disable').click();
    await page.waitForFunction(() => document.querySelector('#silent-status').textContent.startsWith('Disabled'));
    assert.equal((await context.request.post(base + '/api/brain/resume')).status(), 200);
    await page.waitForFunction(() => document.querySelector('#tts-status').textContent.includes('Synthesis: enabled'));
    await page.locator('#tts-stop').click();
    await page.waitForFunction(() => document.querySelector('#tts-status').textContent.includes('Synthesis: disabled'));
    await page.locator('#tts-start').click();
    await page.waitForFunction(() => document.querySelector('#tts-status').textContent.includes('Synthesis: enabled'));
    const firstTts = await (await context.request.get(base + '/api/tts/status')).json();
    await page.locator('#tts-restart').click();
    await page.waitForFunction(() => !document.querySelector('#tts-restart').disabled);
    const secondTts = await (await context.request.get(base + '/api/tts/status')).json();
    assert.notEqual(secondTts.pid, firstTts.pid);
    assert.match(await page.locator('#tts-logs').innerText(), /lifecycle.restart/);
    await page.locator('#tts-trace').fill('unmatched-tts-trace');
    await page.locator('#tts-filters button').click();
    await page.waitForFunction(() => document.querySelector('#tts-logs').textContent === 'No matching TTS events.');
    await page.locator('#tts-trace').fill('');
    await page.locator('#tts-type').selectOption('job');
    await page.locator('#tts-filters button').click();
    await page.waitForFunction(() => document.querySelector('#tts-logs').textContent.includes('job.completed'));
    assert.ok((await page.locator('#tts-logs').innerText()).split('\n').map(JSON.parse).every(row => row.worker_id === 'tts' && row.event_type === 'job'));
    await page.waitForFunction(() => !document.querySelector('#stt-stop').disabled);
    await page.locator('#stt-stop').click();
    try {
      await page.waitForFunction(() => document.querySelector('#stt-status').textContent.includes('Transcription: disabled'));
    } catch (error) {
      console.error(JSON.stringify({stt: await page.locator('#stt-status').innerText(),
        message: await page.locator('#message').innerText(),
        api: await (await context.request.get(base + '/api/stt/status')).json()}));
      throw error;
    }
    assert.match(await page.locator('#stt-logs').innerText(), /lifecycle.stop/);
    await page.locator('#stt-start').click();
    await page.waitForFunction(() => document.querySelector('#stt-status').textContent.includes('Transcription: enabled'));
    const first = await (await context.request.get(base + '/api/stt/status')).json();
    await page.locator('#stt-restart').click();
    await page.waitForFunction(() => !document.querySelector('#stt-restart').disabled);
    const second = await (await context.request.get(base + '/api/stt/status')).json();
    assert.notEqual(second.pid, first.pid);
    await page.locator('#stt-trace').fill('unmatched-browser-trace');
    await page.locator('#stt-filters button').click();
    await page.waitForFunction(() => document.querySelector('#stt-logs').textContent === 'No matching STT events.');
    await page.locator('#stt-trace').fill('');
    await page.locator('#stt-type').selectOption('job');
    await page.locator('#stt-filters button').click();
    await page.waitForFunction(() => document.querySelector('#stt-logs').textContent.includes('job.completed'));
    const rows = (await page.locator('#stt-logs').innerText()).split('\n').map(JSON.parse);
    assert.ok(rows.every(row => row.worker_id === 'stt' && row.event_type === 'job'));
    assert.deepEqual(errors, []);
    const modelState = await (await context.request.get(base + '/api/brain-model/status')).json();
    if (modelState.runtime === 'worker') {
      await page.locator('#model-stop').click();
      await page.waitForFunction(() => document.querySelector('#model-status').textContent.includes('Inference: disabled'));
      await page.locator('#model-start').click();
      await page.waitForFunction(() => document.querySelector('#model-status').textContent.includes('Inference: enabled'));
      const firstModel = await (await context.request.get(base + '/api/brain-model/status')).json();
      await page.locator('#model-restart').click();
      await page.waitForFunction(() => !document.querySelector('#model-restart').disabled);
      const secondModel = await (await context.request.get(base + '/api/brain-model/status')).json();
      assert.notEqual(firstModel.pid, secondModel.pid);
      assert.match(await page.locator('#model-logs').innerText(), /lifecycle.restart/);
      await page.locator('#model-trace').fill('unmatched-model-trace');
      await page.locator('#model-filters button').click();
      await page.waitForFunction(() => document.querySelector('#model-logs').textContent === 'No matching Model events.');
      await page.locator('#model-trace').fill('');
      await page.locator('#model-type').selectOption('job');
      await page.locator('#model-filters button').click();
      await page.waitForFunction(() => document.querySelector('#model-logs').textContent.includes('job.completed'));
      const rows = (await page.locator('#model-logs').innerText()).split('\n').map(JSON.parse);
      assert.ok(rows.every(row => row.worker_id === 'brain-model' && row.event_type === 'job'));
      assert.deepEqual(errors, []);
    }
    console.log(JSON.stringify({browser_stt_controls: true, browser_tts_controls: true, browser_tts_logs: true, browser_log_filters: true}));
  } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
