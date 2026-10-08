const { app, BrowserWindow } = require('electron')
const path = require('node:path')
const assert = require('node:assert/strict')

const pause = (ms = 60) => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    show: false, width: 1280, height: 900,
    webPreferences: {
      offscreen: true, backgroundThrottling: false,
      partition: 'videomatrix-account-ui-regression', contextIsolation: false,
      preload: path.join(__dirname, 'account-ui-preload.cjs'),
    },
  })
  const evaluate = expression => win.webContents.executeJavaScript(expression)
  const setInput = async (label, value) => evaluate(`(() => {
    const input = document.querySelector('input[aria-label=${JSON.stringify(label)}]');
    if (!input) throw new Error('Missing input: ${label}');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
    setter.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
  })()`)
  const waitFor = async (expression) => {
    for (let i = 0; i < 80; i++) {
      if (await evaluate(expression)) return
      await pause()
    }
    throw new Error(`Timed out waiting for ${expression}`)
  }
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    await waitFor('Boolean(document.querySelector("button[aria-label=登录或注册]"))')
    await evaluate('document.querySelector("button[aria-label=登录或注册]").click()')
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("仅在本机保存测试账户")'), true)
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("手机号未经短信验证")'), true)

    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] [role=tab]")].find(node => node.textContent.trim() === "注册").click()')
    await setInput('账户手机号', '13800138000')
    await setInput('账户密码', 'local-test-pass-42')
    await setInput('确认账户密码', 'local-test-pass-42')
    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] button")].find(node => node.textContent.trim() === "注册并登录").click()')
    await waitFor('document.querySelector("[role=dialog][aria-label=账户]")?.innerText.includes("我的账户")')
    assert.equal(await evaluate('window.__accountCalls.some(call => call.path.endsWith("/register") && call.method === "POST" && call.phone === "13800138000" && call.passwordMatchesExpected)'), true)
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("尚未接入云端数据库或短信验证")'), true)
    assert.equal(await evaluate('document.querySelector("input[aria-label=账户密码]")'), null)
    assert.equal(await evaluate(`(() => {
      const persisted = JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } });
      return !persisted.includes('local-test-pass-42') && sessionStorage.getItem('vm-local-test-account-token') !== null;
    })()`), true)

    await evaluate('document.querySelector("button[aria-label=关闭账户窗口]").click()')
    const reloaded = new Promise(resolve => win.webContents.once('did-finish-load', resolve))
    win.webContents.reload()
    await reloaded
    await waitFor('Boolean(document.querySelector("button[aria-label=账户信息]"))')
    assert.equal(await evaluate('window.__accountCalls.some(call => call.path.endsWith("/me") && call.bearer?.startsWith("Bearer "))'), true)
    await evaluate('document.querySelector("button[aria-label=账户信息]").click()')
    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] button")].find(node => node.textContent.trim() === "退出登录").click()')
    await waitFor('Boolean(document.querySelector("button[aria-label=登录或注册]"))')
    assert.equal(await evaluate('window.__accountCalls.some(call => call.path.endsWith("/logout") && call.method === "POST")'), true)
    assert.equal(await evaluate('sessionStorage.getItem("vm-local-test-account-token")'), null)
    assert.equal(await evaluate('document.querySelector("input[aria-label=账户密码]")?.value'), '')

    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] [role=tab]")].find(node => node.textContent.trim() === "登录").click()')
    await setInput('账户手机号', '13800138000')
    await setInput('账户密码', 'local-test-pass-42')
    await evaluate('document.querySelector("[role=dialog][aria-label=账户] button[type=submit]").click()')
    await waitFor('Boolean(document.querySelector("button[aria-label=账户信息]"))')
    assert.equal(await evaluate('window.__accountCalls.some(call => call.path.endsWith("/login") && call.passwordMatchesExpected)'), true)
    console.log('PASS account UI: local disclosure, register, session restore, logout, login, no password persistence')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
