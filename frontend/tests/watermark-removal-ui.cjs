const { chromium } = require('playwright')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

async function main() {
  const root = path.resolve(__dirname, '../dist/renderer')
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + (req.url === '/' ? '/index.html' : req.url.split('?')[0]))
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return res.writeHead(404).end()
    res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    res.end(fs.readFileSync(file))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  let browser
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    const errors = []
    const posts = []
    let polls = 0
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(() => {
      window.electronAPI = {
        getBackendPort: async () => 8765,
        openVideoFiles: async () => ['C:\\sample\\watermarked.mp4'],
        openDirectory: async () => 'C:\\sample\\output',
        openPath: async () => {},
      }
    })
    await page.route('**/api/**', async route => {
      const url = route.request().url()
      if (url.endsWith('/watermark-removal/detect')) {
        return route.fulfill({ json: {
          width: 320, height: 180, region: null, confidence: 0,
          preview: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=',
          message: '未可靠定位角标，请手动框选后处理。',
        } })
      }
      if (url.endsWith('/watermark-removal/jobs') && route.request().method() === 'POST') {
        posts.push(route.request().postDataJSON())
        return route.fulfill({ json: { task_id: 'mock-watermark-1' } })
      }
      if (url.endsWith('/watermark-removal/jobs/mock-watermark-1')) {
        polls++
        return route.fulfill({ json: {
          task_id: 'mock-watermark-1', status: polls < 2 ? 'running' : 'completed',
          progress: polls < 2 ? 53 : 100, current: polls < 2 ? 0 : 1, total: 1,
          output_files: polls < 2 ? [] : ['C:\\sample\\output\\watermarked_去水印_001.mp4'],
          log_lines: ['watermarked.mp4：正在定位水印'], errors: [],
        } })
      }
      return route.fulfill({ json: url.endsWith('/tasks') ? [] : url.endsWith('/health') ? { status: 'ok' } : { files: [], count: 0 } })
    })
    await page.goto(`http://127.0.0.1:${server.address().port}`)
    await page.getByRole('button', { name: '独立去重变换' }).click()
    const panel = page.getByRole('region', { name: '视频去水印' })
    await panel.getByRole('button', { name: '选择视频' }).click()
    await panel.getByRole('button', { name: '预览并定位' }).click()
    await panel.getByText('未可靠定位角标，请手动框选后处理。', { exact: false }).waitFor()
    assert.equal(await panel.getByRole('button', { name: /手动框选/ }).getAttribute('aria-pressed'), 'true')
    const preview = panel.getByLabel('在预览画面中拖动框选水印区域')
    const box = await preview.boundingBox()
    assert.ok(box && box.width > 100 && box.height > 40)
    await page.mouse.move(box.x + box.width * .7, box.y + box.height * .05)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width * .9, box.y + box.height * .2, { steps: 5 })
    await page.mouse.up()
    await panel.getByRole('checkbox', { name: '我拥有这些视频的版权或已取得处理授权' }).check()
    await panel.getByRole('button', { name: '开始去水印' }).click()
    await page.waitForFunction(() => document.body.innerText.includes('已生成 1 个视频'))
    assert.equal(posts.length, 1)
    assert.equal(posts[0].mode, 'manual')
    assert.equal(posts[0].authorized, true)
    assert.equal(posts[0].reference_width, 320)
    assert.equal(posts[0].reference_height, 180)
    assert.ok(posts[0].region.width >= 4 && posts[0].region.height >= 4)
    await panel.getByRole('button', { name: /自动定位/ }).click()
    await panel.getByRole('button', { name: '开始去水印' }).click()
    await page.waitForFunction(() => document.body.innerText.includes('已生成 1 个视频'))
    assert.equal(posts.length, 2)
    assert.equal(posts[1].mode, 'auto')
    assert.equal(posts[1].region, undefined)
    assert.deepEqual(errors, [])
    console.log('PASS watermark removal UI: input, detection fallback, manual region, automatic mode, authorization, task progress')
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
