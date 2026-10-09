const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1280, height: 1080,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-header-help-regression', contextIsolation: false,
      preload: path.join(__dirname, 'header-help-ui-preload.cjs'),
    },
  })
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    const evaluate = async expression => {
      try { return await win.webContents.executeJavaScript(expression) }
      catch (error) { throw new Error(`Renderer expression failed: ${expression}\n${error}`) }
    }
    const assertQrFullyVisible = async dialogLabel => {
      const selector = JSON.stringify(`[role="dialog"][aria-label="${dialogLabel}"] img`)
      const image = await evaluate(`(async () => {
        const image = document.querySelector(${selector})
        await image.decode()
        const bounds = image.getBoundingClientRect()
        return {
          naturalWidth: image.naturalWidth,
          naturalHeight: image.naturalHeight,
          objectFit: getComputedStyle(image).objectFit,
          width: bounds.width,
          height: bounds.height,
          right: bounds.right,
          bottom: bounds.bottom,
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          unobscured: document.elementFromPoint(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2) === image,
        }
      })()`)
      assert.ok(image.naturalWidth > 0 && image.naturalHeight > 0, `${dialogLabel} image loads`)
      assert.equal(image.objectFit, 'contain', `${dialogLabel} preserves the entire portrait image`)
      assert.ok(image.width > 0 && image.height > 0, `${dialogLabel} image has visible size`)
      assert.ok(image.right <= image.viewportWidth + 1 && image.bottom <= image.viewportHeight + 1,
        `${dialogLabel} image fits the window: ${JSON.stringify(image)}`)
      assert.ok(image.unobscured, `${dialogLabel} image is not covered by another panel: ${JSON.stringify(image)}`)
    }
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.body.innerText.includes("联系我")')) break
      await pause()
    }
    assert.equal(await evaluate('document.body.innerText.includes("联系我")'), true)
    assert.equal(await evaluate('document.body.innerText.includes("巨能跑pro版")'), true)
    assert.equal(await evaluate('Boolean(document.querySelector("button[aria-label=检查更新]"))'), true)
    await evaluate('document.querySelector("button[aria-label=检查更新]").click()')
    await pause()
    assert.equal(await evaluate(`document.querySelector('[role="dialog"][aria-label="版本更新"]')?.innerText.includes('您已经是最新版本啦')`), true)
    await evaluate('document.querySelector("button[aria-label=关闭更新面板]").click()')
    await evaluate('document.querySelector("[aria-label=显示联系方式]").click()')
    assert.equal(await evaluate("document.querySelector('[role=dialog][aria-label=联系我] img')?.getAttribute('alt') === 'NaiKio 的微信联系人二维码'"), true)
    await assertQrFullyVisible('联系我')
    await evaluate('document.querySelector("[role=dialog][aria-label=联系我] [role=tab][aria-selected=false]").click()')
    assert.equal(await evaluate("document.querySelector('[role=dialog][aria-label=联系我] img')?.getAttribute('alt') === '徐学志的飞书联系人二维码'"), true)
    await assertQrFullyVisible('联系我')
    win.setSize(700, 520)
    await pause(100)
    await assertQrFullyVisible('联系我')
    await evaluate('document.querySelector("[aria-label=隐藏联系方式]").click()')
    await evaluate('document.querySelector("[aria-label=赞助我]").click()')
    assert.equal(await evaluate(`(() => {
      const dialog = document.querySelector('[role=dialog][aria-label=赞助我]')
      const overlay = dialog?.parentElement
      const bounds = dialog?.getBoundingClientRect()
      return overlay?.parentElement === document.body && bounds &&
        Math.abs((bounds.top + bounds.bottom) / 2 - window.innerHeight / 2) < 12
    })()`), true, 'sponsor dialog is centered in the viewport, not the topbar')
    assert.equal(await evaluate("document.querySelector('[role=dialog][aria-label=赞助我] img')?.getAttribute('alt') === '支付宝赞助二维码'"), true)
    await assertQrFullyVisible('赞助我')
    await evaluate('document.querySelector("[role=dialog][aria-label=赞助我] [role=tab][aria-selected=false]").click()')
    assert.equal(await evaluate("document.querySelector('[role=dialog][aria-label=赞助我] img')?.getAttribute('alt') === '微信支付赞助二维码'"), true)
    await assertQrFullyVisible('赞助我')
    await evaluate('document.querySelector("[aria-label=关闭赞助面板]").click()')
    win.setSize(1280, 1080)
    await pause(100)
    assert.equal(await evaluate(`(() => {
      const row = document.querySelector('input[aria-label="BGM 路径"]')?.closest('.group')
      return row && ![...row.querySelectorAll('button')].some(button => button.textContent.trim() === '视频')
    })()`), true, 'BGM does not offer a video picker')
    await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim() === "视频（可多选）").click()')
    assert.equal(await evaluate(`document.querySelector('input[aria-label="Hook 首段"]')?.value`), 'C:\\hooks\\first.mp4; C:\\hooks\\second.mov')
    assert.equal(await evaluate('JSON.stringify(window.__videoPickerArgs.extensions)'), '["mp4","mov"]')
    await evaluate(`document.querySelector('input[aria-label="Hook 首段"]').closest('.group').querySelector('button').click()`)
    assert.equal(await evaluate('JSON.stringify(window.__openedPaths)'), '["C:\\\\hooks"]')
    await evaluate('document.querySelector("[aria-label=打开重叠率说明]").click()')
    assert.equal(await evaluate('document.querySelector("[aria-label=打开重叠率说明]").getAttribute("aria-expanded")'), 'true')
    assert.equal(await evaluate('document.querySelector("#overlap-rate-help").innerText.includes("0%") && document.querySelector("#overlap-rate-help").innerText.includes("30%")'), true)
    await evaluate('document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))')
    assert.equal(await evaluate('document.querySelector("#overlap-rate-help")'), null)
    assert.equal(await evaluate(`(() => {
      const output = [...document.querySelectorAll('h2')].find(node => node.textContent.trim() === '输出')
      const panel = output?.closest('div[class*="overflow-y-auto"]')
      return Boolean(panel && output && getComputedStyle(panel).overflowY === 'auto')
    })()`), true)
    await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent.trim() === "按原素材时长").click()')
    await pause(600)
    assert.equal(await evaluate('document.body.innerText.includes("14.0–17.0s")'), true)
    assert.equal(await evaluate('document.querySelector("input[aria-label=首段最短秒数]").disabled'), true)
    assert.equal(await evaluate('document.querySelector("input[aria-label=Hook]").value'), '1')
    await evaluate('[...document.querySelectorAll("label")].find(node => node.textContent.trim() === "按原素材时长").click()')
    await pause()
    assert.equal(await evaluate('document.querySelector("input[aria-label=Hook]").value'), '0.5')
    await pause(500)
    win.webContents.invalidate()
    await pause(200)
    fs.mkdirSync(path.join(__dirname, '../release'), { recursive: true })
    fs.writeFileSync(path.join(__dirname, '../release/header-help-ui-test.png'), (await win.webContents.capturePage()).toPNG())
    console.log('PASS contact, help, full-hook switch disables inputs and restores overlap, output visibility')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
