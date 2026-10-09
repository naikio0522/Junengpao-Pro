const { app, BrowserWindow } = require('electron')
const assert = require('node:assert/strict')
const path = require('node:path')

const pause = (ms = 50) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1100, height: 800,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-required-update-regression', contextIsolation: false,
      preload: path.join(__dirname, 'required-update-ui-preload.cjs'),
    },
  })
  const evaluate = expression => win.webContents.executeJavaScript(expression)
  const waitFor = async expression => {
    for (let i = 0; i < 80; i++) {
      if (await evaluate(expression)) return
      await pause()
    }
    throw new Error(`Timed out waiting for ${expression}`)
  }
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    await waitFor('Boolean(document.querySelector("[aria-label=必须更新巨能跑pro版]"))')
    assert.equal(await evaluate('Boolean(document.querySelector("button[aria-label=检查更新]"))'), false)
    assert.equal(await evaluate('document.querySelector("[aria-label=必须更新巨能跑pro版] button").disabled'), true,
      'running jobs keep the installation action disabled')
    await evaluate('window.__activeCount = 0')
    await waitFor('document.querySelector("[aria-label=必须更新巨能跑pro版] button").disabled === false')
    await evaluate('document.querySelector("[aria-label=必须更新巨能跑pro版] button").click()')
    await waitFor('document.body.innerText.includes("已取消安装")')
    assert.equal(await evaluate('window.__installCalls'), 1)
    await evaluate('window.__policyMode = "cached"; [...document.querySelectorAll("button")].find(button => button.innerText === "重新检查").click()')
    await waitFor('document.body.innerText.includes("上次在线确认")')
    assert.equal(await evaluate('Boolean(document.querySelector("[aria-label=必须更新巨能跑pro版]"))'), true)
    await evaluate('window.__policyMode = "offline"; [...document.querySelectorAll("button")].find(button => button.innerText === "重新检查").click()')
    await waitFor('Boolean(document.querySelector("button[aria-label=检查更新]"))')
    assert.equal(await evaluate('document.body.innerText.includes("自动检查更新失败，软件仍可使用")'), true)
    console.log('PASS required update gate waits for jobs, retains cached policy, and handles first offline checks')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
