const { chromium } = require('playwright')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')

async function main() {
  const root = path.resolve(__dirname, '../dist/renderer')
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + (req.url === '/' ? '/index.html' : req.url.split('?')[0]))
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return void res.writeHead(404).end()
    res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    res.end(fs.readFileSync(file))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))

  let browser
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
    const errors = []
    let created = 0
    let taskProgress = 37
    let taskStatus = 'running'
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', route => {
      const url = route.request().url()
      if (url.endsWith('/tasks') && route.request().method() === 'POST') {
        created += 1
        return route.fulfill({ json: { task_id: `task-${created}`, message: 'ok' } })
      }
      if (url.endsWith('/tasks')) {
        const oldTask = { task_id: 'older-task', task_name: '旧任务', status: 'running', progress: 99, current: 0, total: 1, message: '', log_lines: [], output_files: [] }
        const tasks = created ? [...Array.from({ length: created }, (_, index) => ({
          ...oldTask,
          task_id: `task-${index + 1}`,
          task_name: `新任务 ${index + 1}`,
          progress: index + 1 === created ? taskProgress : 80,
          status: index + 1 === created ? taskStatus : 'running',
        })), oldTask] : [oldTask]
        return route.fulfill({ json: tasks })
      }
      return route.fulfill({ json: url.endsWith('/health') ? { status: 'ok' } : { files: [], count: 0 } })
    })

    await page.goto(`http://127.0.0.1:${server.address().port}`)
    await page.getByRole('textbox', { name: 'Hook 首段', exact: true }).fill('C:\\sample\\hook')
    await page.getByRole('textbox', { name: '文件夹', exact: true }).fill('C:\\sample\\body')
    await page.getByRole('textbox', { name: '输出目录', exact: true }).fill('C:\\sample\\out')
    const renderButton = page.getByRole('button', { name: '启动渲染', exact: true })
    assert.equal(await page.getByRole('button', { name: '渲染设置', exact: true }).innerText(), '渲染设置')
    await renderButton.click()
    const progress = page.getByRole('progressbar', { name: '启动渲染进度' })
    await page.waitForFunction(() => document.querySelector('[aria-label="启动渲染进度"]')?.getAttribute('aria-valuenow') === '37')
    assert.equal(await progress.getAttribute('aria-valuenow'), '37')
    assert.equal(await renderButton.locator('[role="progressbar"]').count(), 1)
    taskProgress = 53
    await page.waitForFunction(() => document.querySelector('[aria-label="启动渲染进度"]')?.getAttribute('aria-valuenow') === '53')
    await renderButton.click()
    taskProgress = 11
    await page.waitForFunction(() => document.querySelector('[aria-label="启动渲染进度"]')?.getAttribute('aria-valuenow') === '11')
    assert.equal(created, 2)
    taskStatus = 'completed'
    taskProgress = 100
    await page.waitForFunction(() => document.querySelector('[aria-label="启动渲染进度"]')?.getAttribute('aria-valuenow') === '100')
    await page.waitForFunction(() => document.querySelector('[aria-label="启动渲染进度"]') === null, { timeout: 5000 })
    assert.deepEqual(errors, [])
    console.log('Render button tracks the newest task and keeps progress inside the button')
  } finally {
    if (browser) await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
