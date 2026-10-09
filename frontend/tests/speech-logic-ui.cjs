const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const assert = require('node:assert/strict')

const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1280, height: 900,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-speech-logic-regression', contextIsolation: false,
      preload: path.join(__dirname, 'speech-logic-ui-preload.cjs'),
    },
  })
  const evaluate = expression => win.webContents.executeJavaScript(expression).catch(error => {
    throw new Error(`Renderer expression failed: ${expression}\n${error.stack || error}`)
  })
  const setInput = async (label, value) => evaluate(`(() => {
    const input = document.querySelector('input[aria-label=${JSON.stringify(label)}]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`)
  const click = async label => evaluate(`[...document.querySelectorAll('button')].find(node => node.textContent.trim() === ${JSON.stringify(label)}).click()`)
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.body.innerText.includes("口播编排")')) break
      await pause()
    }
    assert.equal(await evaluate('document.body.innerText.includes("核对产品是否一致、台词是否通顺")'), true)
    assert.equal(await evaluate('document.querySelector("input[aria-label=首段最短秒数]") === null'), true)
    assert.equal(await evaluate('document.querySelector("input[aria-label=后段最短秒数]") === null'), true)
    assert.equal(await evaluate('document.querySelector("input[aria-label=Hook]") === null'), true)
    assert.equal(await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim() === "分组").disabled'), true)

    await setInput('文件夹', 'C:/clips/hook/')
    await click('预检产能')
    await pause()
    assert.equal(await evaluate('window.__speechPreflightRequests.length'), 0)
    assert.equal(await evaluate('document.body.innerText.includes("Hook 与 Body 目录不能相同")'), true)

    await setInput('文件夹', 'C:\\clips\\body')
    await setInput('话段数', '7')
    await click('预检产能')
    await pause()
    assert.equal(await evaluate('window.__speechPreflightRequests.length'), 0)
    assert.equal(await evaluate('document.body.innerText.includes("2–6 段完整话段")'), true)

    await setInput('话段数', '5')
    await setInput('Hook声', '0')
    await click('预检产能')
    await pause()
    assert.equal(await evaluate('window.__speechPreflightRequests.length'), 0)
    assert.equal(await evaluate('document.body.innerText.includes("原声音量设为大于 0%")'), true)

    await setInput('Hook声', '80')
    await click('预检产能')
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.querySelector("[aria-label=口播预览]")?.textContent.includes("成片话术")')) break
      await pause()
    }
    assert.equal(await evaluate('window.__speechPreflightRequests.length'), 1)
    assert.equal(await evaluate('window.__speechPreflightRequests[0].selection_mode'), 'speech_logic')
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("Hook 首段：完整表达")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("Body 后段：顺接话术")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("喝凉水牙酸？因为牙本质暴露。用这支牙膏保护敏感牙。")'), true)
    await evaluate('document.querySelector("[aria-label=口播预览] summary").click()')
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("0.0–2.0s")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("需复核")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("句末由规则推断，请复听")'), true)

    await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim().startsWith("渲染设置")).click()')
    assert.equal(await evaluate('document.body.innerText.includes("成品去重变换")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=不做保底混剪]")?.getAttribute("data-state")'), 'unchecked')
    await evaluate('document.querySelector("[aria-label=不做保底混剪]").click()')
    assert.equal(await evaluate('JSON.parse(localStorage.getItem("vm-config")).no_fallback_mix'), true)

    await setInput('产品（可选）', '色修牙膏')
    await pause()
    await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim().startsWith("口播预览")).click()')
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("修改素材或设置后需重新预检")'), true)
    await evaluate('window.__speechFallback = true')
    await click('预检产能')
    for (let i = 0; i < 50; i++) {
      if (await evaluate('document.querySelector("[aria-label=口播预览]")?.textContent.includes("非分级混剪")')) break
      await pause()
    }
    assert.equal(await evaluate('window.__speechPreflightRequests.at(-1).no_fallback_mix'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("未验证的转录、产品或价促内容可能进入成片")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("未取得可靠台词；请复听原声")'), true)
    assert.equal(await evaluate('document.querySelector("[aria-label=口播预览]").textContent.includes("没有可用口播转录")'), true)
    await click('独立去重变换')
    await pause(380)
    assert.equal(await evaluate('document.querySelector("[aria-label=已有视频去重变换]") !== null'), true)
    assert.equal(await evaluate('document.body.innerText.includes("拖入视频或文件夹")'), true)
    assert.equal(await evaluate('document.querySelector("[data-testid=workflow-pane-mix]").getAttribute("aria-hidden")'), 'true')
    assert.equal(await evaluate('document.querySelector("[data-testid=workflow-pane-dedup]").getAttribute("aria-hidden")'), 'false')
    await evaluate(`(() => {
      const file = new File(['sample'], 'sample.mp4', { type: 'video/mp4' });
      Object.defineProperty(file, 'path', { value: 'C:/clips/sample.mp4' });
      const event = new Event('drop', { bubbles: true, cancelable: true });
      Object.defineProperty(event, 'dataTransfer', { value: { files: [file] } });
      document.querySelector('[aria-label="拖入视频或文件夹"]').dispatchEvent(event);
    })()`)
    assert.equal(await evaluate('document.body.innerText.includes("已添加 1 个入口")'), true)
    assert.equal(await evaluate('[...document.querySelectorAll("button")].find(node => node.textContent.trim() === "开始去重变换").disabled'), false)
    await click('口播逻辑（测试版v0.1）')
    await pause(380)
    assert.equal(await evaluate('document.querySelector("[data-testid=workflow-pane-dedup]").getAttribute("aria-hidden")'), 'true')
    assert.equal(await evaluate('document.querySelector("[data-testid=workflow-pane-mix]").getAttribute("aria-hidden")'), 'false')
    assert.equal(await evaluate('document.querySelector("input[aria-label=文件夹]") !== null'), true)
    console.log('PASS speech logic UI: constraints, previewed transcripts and plan, stale preview cleared')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
