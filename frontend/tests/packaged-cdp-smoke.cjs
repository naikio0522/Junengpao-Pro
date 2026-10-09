// Optional, non-installing smoke test for a separately launched packaged app.
// Launch win-unpacked with --remote-debugging-port=19347, then run this script.
const assert = require('node:assert/strict')

async function main() {
  const port = Number(process.env.VM_CDP_PORT || 19347)
  const targets = await fetch(`http://127.0.0.1:${port}/json/list`).then(response => response.json())
  const page = targets.find(target => target.type === 'page' && target.url.includes('resources/app.asar/'))
  assert.ok(page, 'Packaged application page is not available in CDP')
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  let nextId = 0
  const pending = new Map()
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data)
    if (!pending.has(message.id)) return
    const { resolve, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) reject(new Error(message.error.message))
    else resolve(message.result)
  })
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
    return result.result.value
  }
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
  try {
    assert.equal(await evaluate('document.body.innerText.includes("巨能跑pro版")'), true)
    const apiGuard = await evaluate(`(async () => {
      const port = await window.electronAPI.getBackendPort()
      const url = 'http://127.0.0.1:' + port + '/api/health'
      const denied = await fetch(url).then(response => response.status)
      const token = await window.electronAPI.getBackendToken()
      const allowed = await fetch(url, { headers: { 'X-VideoMatrix-Token': token } })
        .then(response => response.status)
      return { denied, allowed }
    })()`)
    assert.deepEqual(apiGuard, { denied: 403, allowed: 200 })
    assert.equal(await evaluate('Boolean(document.querySelector("button[aria-label=检查更新]"))'), true)
    if (await evaluate('document.querySelector("button[aria-label=检查更新]").getAttribute("aria-expanded") === "true"')) {
      await evaluate('document.querySelector("button[aria-label=检查更新]").click()')
    }
    await evaluate('document.querySelector("button[aria-label=检查更新]").click()')
    let panelText = ''
    for (let attempt = 0; attempt < 30; attempt++) {
      panelText = await evaluate(`document.querySelector('[role="dialog"][aria-label="版本更新"]')?.innerText || ""`)
      if (panelText.includes('您已经是最新版本啦')) break
      await sleep(500)
    }
    assert.match(panelText, /您已经是最新版本啦/)
    console.log('PASS packaged v0.1.7 UI, update button, and no available update notice')
  } finally {
    socket.close()
  }
}

main().catch(error => { console.error(error); process.exitCode = 1 })
