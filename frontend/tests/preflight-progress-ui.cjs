const { chromium } = require('playwright')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

async function main() {
  const root = path.resolve(__dirname, '../dist/renderer')
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + (req.url === '/' ? '/index.html' : req.url.split('?')[0]))
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404).end()
      return
    }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    res.end(fs.readFileSync(file))
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  let browser
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    const errors = []
    let preflightCount = 0
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/**', (route) => {
      const url = route.request().url()
      if (url.endsWith('/preflight')) {
        assert.equal(route.request().headers().accept.includes('text/event-stream'), true)
        preflightCount += 1
        const result = { ok: true, capacity: 2, report: [{ name: '样例', ok: true, capacity: 2, message: '预检通过' }] }
        if (preflightCount === 1) {
          const events = [
            { type: 'progress', percent: 0, message: '待检查 1 个素材库' },
            { type: 'progress', percent: 45, message: '样例：探测媒体 2/2' },
            { type: 'progress', percent: 100, message: '全部素材预检完成' },
            { type: 'result', result },
          ]
          return route.fulfill({ status: 200, contentType: 'text/event-stream',
            body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('') })
        }
        return route.fulfill({ json: result })
      }
      return route.fulfill({ json: url.endsWith('/tasks') ? [] : url.endsWith('/health') ? { status: 'ok' } : { files: [], count: 0 } })
    })
    await page.goto(`http://127.0.0.1:${server.address().port}`)
    await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).fill('C:\\sample\\hook')
    await page.getByRole('textbox', { name: '文件夹', exact: true }).fill('C:\\sample\\body')
    await page.getByRole('textbox', { name: '输出目录', exact: true }).fill('C:\\sample\\out')

    for (let attempt = 1; attempt <= 2; attempt += 1) {
      await page.getByRole('button', { name: '预检产能', exact: true }).click()
      const progress = page.getByRole('progressbar', { name: '预检产能进度' })
      await progress.waitFor()
      await page.waitForFunction(() => document.querySelector('[aria-label="预检产能进度"]')?.getAttribute('aria-valuenow') === '100')
      assert.equal(await progress.getAttribute('aria-valuenow'), '100')
      await page.getByRole('button', { name: '预检产能', exact: true }).waitFor()
    }
    assert.equal(preflightCount, 2)
    assert.deepEqual(errors, [])
    console.log('Preflight SSE and JSON fallback UI smoke passed')
  } finally {
    if (browser) await browser.close()
    await new Promise((resolve) => server.close(resolve))
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1 })
