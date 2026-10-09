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
    let benchmarkCount = 0
    page.on('pageerror', (error) => errors.push(error.message))
    await page.route('**/api/**', (route) => {
      const url = route.request().url()
      if (url.endsWith('/benchmark')) {
        assert.equal(route.request().headers().accept.includes('text/event-stream'), true)
        benchmarkCount += 1
        const result = { results: { 1: { concurrent: 1, total_time: 1.3, avg_per_video: 1.3 } }, best_concurrent: 1, warnings: [] }
        if (benchmarkCount === 1) {
          const events = [
            { type: 'progress', percent: 0, message: '准备压测素材' },
            { type: 'progress', percent: 42, message: '2 路并发：已完成 1/2 条' },
            { type: 'progress', percent: 42, message: '2 路并发：已完成 1/2 条（已用 30 秒）' },
            { type: 'progress', percent: 100, message: '智能压测完成' },
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

    await page.getByRole('button', { name: '智能压测', exact: true }).click()
    await page.waitForFunction(() => document.body.innerText.includes('[压测] 42%'))
    await page.waitForFunction(() => document.body.innerText.includes('[压测完成]'))
    assert.equal(await page.getByText('>>> [压测] 42% · 2 路并发：已完成 1/2 条').count(), 1)
    assert.equal((await page.locator('body').innerText()).includes('[压测] 正在测试 95%'), false)
    await page.getByRole('button', { name: '智能压测', exact: true }).waitFor()
    await page.getByRole('button', { name: '智能压测', exact: true }).click()
    await page.waitForFunction(() => (document.body.innerText.match(/\[压测完成\]/g) || []).length === 2)
    assert.equal(benchmarkCount, 2)
    assert.deepEqual(errors, [])
    console.log('Benchmark real SSE progress and legacy JSON fallback UI smoke passed')
  } finally {
    if (browser) await browser.close()
    await new Promise((resolve) => server.close(resolve))
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1 })
