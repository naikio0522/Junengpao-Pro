const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const assert = require('node:assert/strict')

async function inspect(win) {
  return win.webContents.executeJavaScript(`(() => {
    const task = document.querySelector('[aria-label="你想让我怎么做？"]');
    const log = document.querySelector('[aria-label="运行日志"]');
    const taskRect = task?.getBoundingClientRect();
    const logRect = log?.getBoundingClientRect();
    return {
      taskRight: taskRect?.right,
      logRight: logRect?.right,
      columns: taskRect && logRect ? logRect.top - taskRect.bottom : null,
      viewportWidth: document.documentElement.clientWidth,
      contentWidth: document.documentElement.scrollWidth,
      logWidth: logRect?.width,
    };
  })()`)
}

app.whenReady().then(async () => {
  const win = new BrowserWindow({ show: false, width: 1280, height: 900,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      preload: path.join(__dirname, 'header-help-ui-preload.cjs'),
      partition: 'videomatrix-responsive-regression',
      contextIsolation: false,
    } })
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    let wide
    for (let attempt = 0; attempt < 50; attempt++) {
      wide = await inspect(win)
      if (Number.isFinite(wide.taskRight) && Number.isFinite(wide.logRight)) break
      await new Promise(resolve => setTimeout(resolve, 60))
    }
    assert(Number.isFinite(wide.taskRight) && Number.isFinite(wide.logRight), JSON.stringify(wide))
    assert(Math.abs(wide.taskRight - wide.logRight) <= 2, `desktop panel edges differ: ${JSON.stringify(wide)}`)
    assert(wide.contentWidth <= wide.viewportWidth + 2, `desktop horizontal overflow: ${JSON.stringify(wide)}`)
    win.setSize(760, 600)
    await new Promise(resolve => setTimeout(resolve, 200))
    const narrow = await inspect(win)
    assert(narrow.logWidth > 300, `narrow log is too small: ${JSON.stringify(narrow)}`)
    assert(narrow.columns >= -2, `narrow panels did not stack: ${JSON.stringify(narrow)}`)
    assert(narrow.contentWidth <= narrow.viewportWidth + 2, `narrow horizontal overflow: ${JSON.stringify(narrow)}`)
    console.log('PASS responsive layout: aligned wide edges, stacked narrow panels, no horizontal overflow')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
