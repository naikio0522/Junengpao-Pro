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
    assert.equal(await evaluate(`(() => {
      const dialog = document.querySelector('[role=dialog][aria-label=账户]')
      const overlay = dialog?.parentElement
      const bounds = dialog?.getBoundingClientRect()
      return overlay?.parentElement === document.body && bounds &&
        Math.abs((bounds.top + bounds.bottom) / 2 - window.innerHeight / 2) < 12
    })()`), true, 'account dialog is centered in the viewport, not the topbar')
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("仅在本机保存测试账户")'), true)
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("手机号未经短信验证")'), true)

    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] [role=tab]")].find(node => node.textContent.trim() === "注册").click()')
    await setInput('账户手机号', '13800138000')
    await setInput('账户密码', 'local-test-pass-42')
    await setInput('确认账户密码', 'local-test-pass-42')
    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] button")].find(node => node.textContent.trim() === "注册并登录").click()')
    await waitFor('document.querySelector("[role=dialog][aria-label=账户]")?.innerText.includes("我的账户")')
    assert.equal(await evaluate('window.__accountCalls.some(call => call.path.endsWith("/register") && call.method === "POST" && call.phone === "13800138000" && call.passwordMatchesExpected)'), true)
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("账号目前保存在本机测试库")'), true)
    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] button")].find(node => node.textContent.trim() === "会员充值").click()')
    await waitFor('document.querySelector("[role=dialog][aria-label=账户]")?.innerText.includes("正式商户订单和回调验签服务未接通")')
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("正式商户订单和回调验签服务未接通")'), true)
    assert.equal(await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] button")].filter(node => /支付宝支付|微信支付/.test(node.textContent)).every(node => node.disabled)'), true)
    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] button")].find(node => node.textContent.trim() === "返回账户").click()')
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

    await evaluate('sessionStorage.setItem("__test_account_mode", "cloud"); sessionStorage.setItem("__test_account_me_status", "503")')
    const unavailableReload = new Promise(resolve => win.webContents.once('did-finish-load', resolve))
    win.webContents.reload()
    await unavailableReload
    await waitFor('Boolean(document.querySelector("button[aria-label=登录或注册]"))')
    await evaluate('document.querySelector("button[aria-label=登录或注册]").click()')
    await waitFor('document.querySelector("[role=dialog][aria-label=账户]")?.innerText.includes("登录凭证已保留")')
    assert.equal(await evaluate('sessionStorage.getItem("vm-local-test-account-token") !== null'), true, '503 must not destroy a valid session')
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("账号由云端服务保存")'), true)
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("仅在本机保存测试账户")'), false)
    await evaluate('sessionStorage.removeItem("__test_account_me_status"); [...document.querySelectorAll("[role=dialog][aria-label=账户] button")].find(node => node.textContent.trim() === "重试检查").click()')
    await waitFor('Boolean(document.querySelector("button[aria-label=账户信息]"))')
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("账号由云端服务保存")'), true)
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("账号目前保存在本机测试库")'), false)

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
    await evaluate('sessionStorage.setItem("__test_account_me_status", "401")')
    const expiredReload = new Promise(resolve => win.webContents.once('did-finish-load', resolve))
    win.webContents.reload()
    await expiredReload
    await waitFor('sessionStorage.getItem("vm-local-test-account-token") === null')
    await evaluate('document.querySelector("button[aria-label=登录或注册]").click()')
    await waitFor('document.querySelector("[role=dialog][aria-label=账户]")?.innerText.includes("登录已失效")')
    await evaluate('[...document.querySelectorAll("[role=dialog][aria-label=账户] [role=tab]")].find(node => node.textContent.trim() === "注册").click()')
    assert.equal(await evaluate('document.querySelector("[role=dialog][aria-label=账户]").innerText.includes("注册云端账户")'), true)
    console.log('PASS account UI: local/cloud disclosure, transient 503 retry, 401 cleanup, register, logout, login, no password persistence')
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
