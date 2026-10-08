const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const assert = require('node:assert/strict')

const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1440, height: 900,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-range-input-regression', contextIsolation: false,
      preload: path.join(__dirname, 'range-input-ui-preload.cjs'),
    },
  })
  win.webContents.on('console-message', (_event, level, message) => {
    if (level >= 2) console.error('renderer:', message)
  })
  const evaluate = async expression => {
    try { return await win.webContents.executeJavaScript(expression) }
    catch (error) { console.error('evaluate failed:', expression); throw error }
  }
  const setInput = async (label, value) => evaluate(`(() => {
    const input = document.querySelector('input[aria-label=${JSON.stringify(label)}]');
    if (!input) throw new Error('Missing input: ' + ${JSON.stringify(label)});
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`)
  const click = async label => evaluate(`(() => {
    const button = [...document.querySelectorAll('button')].find(node => node.textContent.trim() === ${JSON.stringify(label)});
    if (!button) throw new Error('Missing button: ' + ${JSON.stringify(label)});
    button.click();
  })()`)
  const hasText = text => evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`)
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    for (let i = 0; i < 50; i++) {
      if (await evaluate(`document.querySelector('input[aria-label="Hook 首段"]') !== null`)) break
      await pause()
    }

    assert.equal(await evaluate(`document.querySelector('input[aria-label="首段最短秒数"]').value`), '4')
    assert.equal(await evaluate(`document.querySelector('input[aria-label="首段最长秒数"]').value`), '4')
    assert.equal(await evaluate(`document.querySelector('input[aria-label="后段最短秒数"]').value`), '6')
    assert.equal(await evaluate(`document.querySelector('input[aria-label="后段最长秒数"]').value`), '6')
    for (const label of ['Hook 首段', '文件夹', '输出目录']) {
      assert.equal(await evaluate(`(() => {
        const input = document.querySelector('input[aria-label=${JSON.stringify(label)}]');
        const card = input.parentElement.parentElement;
        const star = card.querySelector('span[aria-label="必填"]');
        return !!star && star.textContent === '*' && star.className.includes('text-hot');
      })()`), true, `${label} should show a red required star`)
    }

    await click('预检产能')
    await pause()
    assert.equal(await hasText('请选择 Hook 首段视频或文件夹（必填）'), true)
    assert.equal(await evaluate('window.__rangePreflightRequests.length'), 0)
    await click('启动渲染')
    await pause()
    assert.equal(await evaluate('window.__rangeTaskRequests.length'), 0)

    assert.equal(await evaluate(`(() => {
      const card = document.querySelector('input[aria-label="Hook 首段"]').parentElement;
      return ['文件夹', '视频'].every(label => [...card.querySelectorAll('button')].some(button => button.textContent.trim() === label));
    })()`), true)
    await evaluate(`(() => {
      const card = document.querySelector('input[aria-label="Hook 首段"]').parentElement;
      [...card.querySelectorAll('button')].find(button => button.textContent.trim() === '文件夹').click();
    })()`)
    await pause()
    assert.equal(await evaluate(`document.querySelector('input[aria-label="Hook 首段"]').value`), 'C:\\clips\\hook-folder')
    await evaluate(`(() => {
      const card = document.querySelector('input[aria-label="Hook 首段"]').parentElement;
      [...card.querySelectorAll('button')].find(button => button.textContent.trim() === '视频').click();
    })()`)
    await pause()
    assert.equal(await evaluate(`document.querySelector('input[aria-label="Hook 首段"]').value`), 'C:\\clips\\single-hook.mp4')
    assert.equal(await evaluate('window.__hookFileFilters[0].extensions.join(",")'), 'mp4,mov')
    await click('预检产能')
    await pause()
    assert.equal(await hasText('请选择输出目录（必填）'), true)
    assert.equal(await evaluate('window.__rangePreflightRequests.length'), 0)

    await setInput('输出目录', 'C:\\clips\\output')
    await click('预检产能')
    await pause()
    assert.equal(await hasText('请选择 Body 后段文件夹（必填）'), true)
    assert.equal(await evaluate('window.__rangePreflightRequests.length'), 0)

    await setInput('文件夹', 'C:\\clips\\body')
    await setInput('首段最短秒数', '2')
    await setInput('首段最长秒数', '1')
    await click('预检产能')
    await pause()
    assert.equal(await hasText('最长秒数不能小于最短秒数'), true)
    assert.equal(await evaluate('window.__rangePreflightRequests.length'), 0)
    await setInput('首段最短秒数', '1')
    await setInput('首段最长秒数', '2')
    await setInput('后段最短秒数', '0.5')
    await setInput('后段最长秒数', '1.5')
    await click('预检产能')
    for (let i = 0; i < 50; i++) {
      if (await evaluate('window.__rangePreflightRequests.length')) break
      await pause()
    }
    assert.equal(await evaluate('window.__rangePreflightRequests.length'), 1)
    const request = await evaluate('window.__rangePreflightRequests[0]')
    assert.equal(request.hook_dir, 'C:\\clips\\single-hook.mp4')
    assert.deepEqual(request.body_dirs, ['C:\\clips\\body'])
    assert.equal(request.base_out_dir, 'C:\\clips\\output')
    assert.deepEqual(
      [request.t_hook_min, request.t_hook_max, request.t_body_min, request.t_body_max],
      [1, 2, 0.5, 1.5],
    )
    assert.deepEqual([request.t_hook, request.t_body], [1, 0.5])
    console.log('PASS range Hook UI: legacy migration, required paths, file picker, min/max request')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
