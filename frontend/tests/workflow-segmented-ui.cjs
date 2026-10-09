const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const assert = require('node:assert/strict')

const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))
const labels = ['口播逻辑（测试版v0.1）', '随机混剪', '独立去重变换', '短视频链接一键去水印', '成片转字幕']
const groupSelector = '[role="group"][aria-label="你想让我怎么做？"]'

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1280, height: 900,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-workflow-segmented-regression', contextIsolation: false,
      preload: path.join(__dirname, 'workflow-segmented-ui-preload.cjs'),
    },
  })
  const evaluate = async expression => {
    try { return await win.webContents.executeJavaScript(expression) }
    catch (error) { throw new Error(`Renderer expression failed: ${expression}\n${error}`) }
  }
  const buttonSelector = index => `${groupSelector} button:nth-of-type(${index + 1})`
  const inspect = () => evaluate(`(() => {
    const group = document.querySelector(${JSON.stringify(groupSelector)})
    const buttons = [...group.querySelectorAll('button')]
    const thumb = document.querySelector('[data-testid="workflow-thumb"]')
    const mix = document.querySelector('[data-testid="workflow-pane-mix"]')
    const dedup = document.querySelector('[data-testid="workflow-pane-dedup"]')
    const link = document.querySelector('[data-testid="workflow-pane-link-watermark"]')
    const subtitle = document.querySelector('[data-testid="workflow-pane-subtitle"]')
    const rect = node => node.getBoundingClientRect().toJSON()
    return {
      labels: buttons.map(button => button.textContent.trim()),
      pressed: buttons.map(button => button.getAttribute('aria-pressed')),
      buttonRects: buttons.map(rect),
      groupRect: rect(group),
      thumbRect: rect(thumb),
      thumbTransition: getComputedStyle(thumb).transitionDuration,
      mix: { hidden: mix.getAttribute('aria-hidden'), visible: getComputedStyle(mix).visibility,
        opacity: getComputedStyle(mix).opacity, transition: getComputedStyle(mix).transitionDuration },
      dedup: { hidden: dedup.getAttribute('aria-hidden'), visible: getComputedStyle(dedup).visibility,
        opacity: getComputedStyle(dedup).opacity, transition: getComputedStyle(dedup).transitionDuration },
      link: { hidden: link.getAttribute('aria-hidden'), visible: getComputedStyle(link).visibility },
      subtitle: { hidden: subtitle.getAttribute('aria-hidden'), visible: getComputedStyle(subtitle).visibility },
      viewportWidth: innerWidth,
    }
  })()`)
  const expectMode = async index => {
    let state
    for (let retry = 0; retry < 50; retry++) {
      state = await inspect()
      const button = state.buttonRects[index]
      const thumb = state.thumbRect
      const centered = Math.abs((thumb.left + thumb.right) / 2 - (button.left + button.right) / 2) < 4
        && Math.abs(thumb.width - button.width) < 4
      if (state.pressed[index] === 'true' && centered
          && state.mix.visible === (index <= 1 ? 'visible' : 'hidden')
          && state.dedup.visible === (index === 2 ? 'visible' : 'hidden')
          && state.link.visible === (index === 3 ? 'visible' : 'hidden')
          && state.subtitle.visible === (index === 4 ? 'visible' : 'hidden')) break
      await pause(80)
    }
    assert.deepEqual(state.labels, labels)
    assert.deepEqual(state.pressed, labels.map((_, position) => position === index ? 'true' : 'false'),
      `exactly one workflow active: ${JSON.stringify(state.pressed)}`)
    assert.equal(state.mix.hidden, index <= 1 ? 'false' : 'true')
    assert.equal(state.dedup.hidden, index === 2 ? 'false' : 'true')
    assert.equal(state.link.hidden, index === 3 ? 'false' : 'true')
    assert.equal(state.subtitle.hidden, index === 4 ? 'false' : 'true')
    assert.equal(state.mix.visible, index <= 1 ? 'visible' : 'hidden')
    assert.equal(state.dedup.visible, index === 2 ? 'visible' : 'hidden')
    assert.equal(state.link.visible, index === 3 ? 'visible' : 'hidden')
    assert.equal(state.subtitle.visible, index === 4 ? 'visible' : 'hidden')
    const button = state.buttonRects[index]
    const thumb = state.thumbRect
    assert.ok(Math.abs((thumb.left + thumb.right) / 2 - (button.left + button.right) / 2) < 4,
      `thumb centered on ${labels[index]}: ${JSON.stringify({ thumb, button })}`)
    assert.ok(Math.abs(thumb.width - button.width) < 4, 'thumb matches active option width')
    return state
  }
  let debuggerAttached = false
  let passed = false
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    for (let retry = 0; retry < 50; retry++) {
      if (await evaluate(`Boolean(document.querySelector(${JSON.stringify(groupSelector)}) && document.querySelector('[data-testid="workflow-thumb"]'))`)) break
      await pause()
    }
    await expectMode(0)
    assert.equal(await evaluate(`Boolean(document.querySelector(${JSON.stringify('input[aria-label="产品（可选）"]')}))`), true)

    await evaluate(`document.querySelector(${JSON.stringify(buttonSelector(1))}).click()`)
    await expectMode(1)
    assert.equal(await evaluate(`Boolean(document.querySelector(${JSON.stringify('input[aria-label="产品（可选）"]')}))`), false)

    await evaluate(`document.querySelector(${JSON.stringify(buttonSelector(2))}).click()`)
    await expectMode(2)
    assert.equal(await evaluate(`Boolean(document.querySelector(${JSON.stringify('[aria-label="已有视频去重变换"]')}))`), true)

    await evaluate(`document.querySelector(${JSON.stringify(buttonSelector(3))}).click()`)
    await expectMode(3)
    assert.equal(await evaluate(`Boolean(document.querySelector(${JSON.stringify('[aria-label="短视频分享链接"]')}))`), true)

    await evaluate(`document.querySelector(${JSON.stringify(buttonSelector(4))}).click()`)
    await expectMode(4)
    assert.equal(await evaluate(`Boolean(document.querySelector(${JSON.stringify('[aria-label="待转字幕成片路径"]')}))`), true)

    await evaluate(`document.querySelector(${JSON.stringify(buttonSelector(0))}).click()`)
    await expectMode(0)

    win.webContents.debugger.attach('1.3')
    debuggerAttached = true
    const send = (method, params) => win.webContents.debugger.sendCommand(method, params)
    const mouse = async (type, x, y, down = false) => {
      await send('Input.dispatchMouseEvent', {
        type, x, y, button: type === 'mouseMoved' && !down ? 'none' : 'left',
        buttons: down ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1,
      })
      await pause(80)
    }
    const buttonCenter = async index => {
      const state = await inspect()
      const rect = state.buttonRects[index]
      return { x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2 }
    }
    const start = await buttonCenter(0)
    const middle = await buttonCenter(1)
    const end = await buttonCenter(4)
    await mouse('mouseMoved', start.x, start.y)
    await expectMode(0) // Hover must not switch workflows.
    await mouse('mousePressed', start.x, start.y, true)
    await mouse('mouseMoved', middle.x, middle.y, true)
    await expectMode(1) // Changes during the drag, not only after release.
    await mouse('mouseMoved', end.x, end.y, true)
    await expectMode(4)
    await mouse('mouseReleased', end.x, end.y)
    await expectMode(4)

    const key = async (name, code) => {
      for (const type of ['keyDown', 'keyUp']) {
        await send('Input.dispatchKeyEvent', { type, key: name, code: name,
          windowsVirtualKeyCode: code, nativeVirtualKeyCode: code })
      }
      await pause(80)
    }
    await evaluate(`document.querySelector(${JSON.stringify(buttonSelector(2))}).focus()`)
    await key('Home', 36)
    await expectMode(0)
    await key('ArrowRight', 39)
    await expectMode(1)
    await key('End', 35)
    await expectMode(4)
    await key('ArrowLeft', 37)
    await expectMode(3)

    win.setSize(700, 520)
    await pause(120)
    const narrow = await expectMode(3)
    assert.ok(narrow.groupRect.right <= narrow.viewportWidth + 1,
      `workflow switch fits narrow window: ${JSON.stringify(narrow.groupRect)}`)
    for (const rect of narrow.buttonRects) {
      assert.ok(rect.left >= 0 && rect.right <= narrow.viewportWidth + 1,
        `workflow button fits narrow window: ${JSON.stringify(rect)}`)
    }

    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] })
    await pause(80)
    const reduced = await inspect()
    const longestTransition = value => Math.max(...value.split(',').map(part => parseFloat(part) || 0))
    assert.ok(longestTransition(reduced.thumbTransition) <= 0.01, 'reduced motion disables thumb animation')
    assert.ok(longestTransition(reduced.mix.transition) <= 0.01, 'reduced motion disables mix-pane animation')
    assert.ok(longestTransition(reduced.dedup.transition) <= 0.01, 'reduced motion disables dedup-pane animation')

    console.log('PASS workflow segmented control: click, live pointer drag, keyboard, content, narrow window, reduced motion')
    passed = true
  } catch (error) {
    console.error(error)
  } finally {
    if (debuggerAttached && !win.isDestroyed()) win.webContents.debugger.detach()
    app.exit(passed ? 0 : 1)
  }
})
