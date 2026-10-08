// Capture actual renderer previews after `npm run build:renderer`.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')
const path = require('node:path')

const pause = ms => new Promise(resolve => setTimeout(resolve, ms))

app.whenReady().then(async () => {
  const output = path.join(__dirname, '../release')
  fs.mkdirSync(output, { recursive: true })
  const win = new BrowserWindow({
    show: false,
    width: 1440,
    height: 900,
    webPreferences: {
      offscreen: true,
      backgroundThrottling: false,
      preload: path.join(__dirname, 'header-help-ui-preload.cjs'),
      partition: 'videomatrix-ui-preview',
      contextIsolation: false,
    },
  })
  try {
    await win.loadFile(path.join(__dirname, '../dist/renderer/index.html'))
    for (let attempt = 0; attempt < 60; attempt++) {
      if (await win.webContents.executeJavaScript('document.body.innerText.includes("巨能跑")')) break
      await pause(60)
    }
    await pause(200)
    const darkButton = await win.webContents.executeJavaScript('document.querySelector("button") && [...document.querySelectorAll("button")].find(button => button.textContent.trim() === "Dark") !== undefined')
    if (darkButton) await win.webContents.executeJavaScript('[...document.querySelectorAll("button")].find(button => button.textContent.trim() === "Dark").click()')
    await pause(120)
    fs.writeFileSync(path.join(output, 'ui-preview-dark.png'), (await win.webContents.capturePage()).toPNG())
    await win.webContents.executeJavaScript('[...document.querySelectorAll("button")].find(button => button.textContent.trim() === "Light").click()')
    await pause(120)
    fs.writeFileSync(path.join(output, 'ui-preview-light.png'), (await win.webContents.capturePage()).toPNG())
    win.setSize(760, 600)
    await pause(150)
    fs.writeFileSync(path.join(output, 'ui-preview-compact.png'), (await win.webContents.capturePage()).toPNG())
    console.log(`Preview images saved to ${output}`)
    app.exit(0)
  } catch (error) {
    console.error(error)
    app.exit(1)
  }
})
