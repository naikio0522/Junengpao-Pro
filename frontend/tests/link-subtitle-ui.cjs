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
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    const errors = [], resolveRequests = [], downloadRequests = [], subtitleRequests = [], tokenHeaders = []
    let polls = 0
    page.on('pageerror', cause => errors.push(cause.message))
    await page.addInitScript(() => {
      window.__copiedText = []
      window.__openedHttps = []
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
        writeText: async text => { window.__copiedText.push(text) },
      } })
      window.electronAPI = {
        getBackendPort: async () => 8765,
        getBackendToken: async () => 'test-desktop-token',
        openDirectory: async () => 'C:\\sample\\output',
        openFile: async (filters) => filters?.[0]?.extensions?.includes('srt')
          ? 'C:\\sample\\timed.srt' : 'C:\\sample\\finished.mp4',
        openPath: async () => {},
        openExternalHttps: async url => { window.__openedHttps.push(url) },
      }
    })
    await page.route('https://img.example.test/**', route => route.fulfill({
      contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=', 'base64'),
    }))
    await page.route('**/api/**', async route => {
      const url = route.request().url()
      tokenHeaders.push(route.request().headers()['x-videomatrix-token'])
      if (url.endsWith('/link-watermark/resolve')) {
        resolveRequests.push(route.request().postDataJSON())
        return route.fulfill({ json: {
          resolve_id: 'resolved-1', platform: 'douyin', title: '授权测试视频',
          thumbnail_url: 'https://img.example.test/cover.png', webpage_url: 'https://www.douyin.com/video/123',
          video_url: 'https://media.example.test/video.mp4', watermark_status: 'unverified',
          warning: '未核验无水印', formats: [{ id: 'hd', label: '高清', width: 1080, height: 1920, ext: 'mp4', filesize: null, watermark_status: 'unverified' }],
        } })
      }
      if (url.endsWith('/link-watermark/jobs') && route.request().method() === 'POST') {
        downloadRequests.push(route.request().postDataJSON())
        return route.fulfill({ json: { task_id: 'download-1' } })
      }
      if (url.endsWith('/link-watermark/jobs/download-1')) {
        polls++
        return route.fulfill({ json: {
          task_id: 'download-1', status: polls < 2 ? 'running' : 'completed',
          progress: polls < 2 ? 40 : 100, stage: polls < 2 ? '下载中' : '下载完成',
          output_files: polls < 2 ? [] : ['C:\\sample\\output\\video.mp4', 'C:\\sample\\output\\video_封面.jpg'], errors: [], log_lines: [],
        } })
      }
      if (url.endsWith('/subtitles/srt')) {
        subtitleRequests.push(route.request().postDataJSON())
        return route.fulfill({ json: {
          video_path: 'C:\\sample\\finished.mp4', srt_path: 'C:\\sample\\finished.srt',
          folder: 'C:\\sample', cue_count: 8, status: 'created',
        } })
      }
      return route.fulfill({ json: url.endsWith('/tasks') ? [] : url.endsWith('/health') ? { status: 'ok' } : { files: [], count: 0 } })
    })
    await page.goto(`http://127.0.0.1:${server.address().port}`)

    await page.getByRole('button', { name: '短视频链接一键去水印' }).click()
    const link = page.getByRole('region', { name: '短视频链接一键去水印' })
    await link.getByLabel('短视频分享链接').fill('https://v.douyin.com/example/')
    assert.equal(await link.getByRole('button', { name: '解析链接' }).isDisabled(), true)
    await link.getByRole('checkbox', { name: /拥有该视频的版权/ }).check()
    await link.getByRole('button', { name: '解析链接' }).click()
    await link.getByText('授权测试视频').waitFor()
    assert.equal(resolveRequests.length, 1)
    assert.equal(resolveRequests[0].authorized, true)
    await link.getByRole('button', { name: '复制标题' }).click()
    await link.getByRole('button', { name: '复制封面地址' }).click()
    await link.getByRole('button', { name: '复制视频源地址' }).click()
    await link.getByRole('button', { name: '打开封面（可另存）' }).click()
    assert.deepEqual(await page.evaluate(() => window.__copiedText), [
      '授权测试视频', 'https://img.example.test/cover.png', 'https://media.example.test/video.mp4',
    ])
    assert.deepEqual(await page.evaluate(() => window.__openedHttps), ['https://img.example.test/cover.png'])
    await link.getByRole('button', { name: '选择' }).click()
    await link.getByRole('checkbox', { name: '同时保存封面到输出文件夹' }).check()
    await link.getByRole('button', { name: '保存视频和封面' }).click()
    await link.getByText('下载完成').waitFor()
    await link.getByRole('button', { name: '打开封面', exact: true }).waitFor()
    assert.equal(downloadRequests.length, 1)
    assert.equal(downloadRequests[0].format_id, 'hd')
    assert.equal(downloadRequests[0].authorized, true)
    assert.equal(downloadRequests[0].output_dir, 'C:\\sample\\output')
    assert.equal(downloadRequests[0].save_cover, true)

    await page.getByRole('button', { name: '成片转字幕' }).click()
    const subtitle = page.getByRole('region', { name: '成片转字幕' })
    await subtitle.getByRole('button', { name: '选择成片' }).click()
    assert.equal(await subtitle.getByLabel('待转字幕成片路径').inputValue(), 'C:\\sample\\finished.mp4')
    await subtitle.getByRole('button', { name: '生成时间轴 SRT' }).click()
    await subtitle.getByText('已生成 8 条字幕').waitFor()
    assert.equal(subtitleRequests.length, 1)
    assert.equal(subtitleRequests[0].video_path, 'C:\\sample\\finished.mp4')

    await page.getByRole('button', { name: '随机混剪' }).click()
    await page.getByRole('button', { name: 'SRT 文件' }).click()
    assert.equal(await page.getByRole('textbox', { name: '字幕', exact: true }).inputValue(), 'C:\\sample\\timed.srt')
    assert.deepEqual(errors, [])
    assert.ok(tokenHeaders.length > 0 && tokenHeaders.every(value => value === 'test-desktop-token'))
    console.log('PASS link/subtitle UI: authorized parse, copy/open/save cover, video save, progress, conversion, single SRT picker')
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(cause => { console.error(cause); process.exitCode = 1 })
