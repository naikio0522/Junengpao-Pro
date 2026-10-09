const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const { createHash } = require('node:crypto')
const { EventEmitter } = require('node:events')
const Module = require('node:module')
const esbuild = require('esbuild')

function load(source) {
  const filename = path.resolve(__dirname, '../', source)
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module)
  mod.paths = Module._nodeModulePaths(path.dirname(filename))
  mod._compile(result.outputFiles[0].text, filename)
  return mod.exports
}

function loadUpdaterWithVersion(version, request, extra = {}) {
  const filename = path.resolve(__dirname, '../src/main/releaseUpdater.ts')
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module)
  mod.paths = Module._nodeModulePaths(path.dirname(filename))
  const originalRequire = mod.require.bind(mod)
  mod.require = id => id === 'electron'
    ? { app: { getVersion: () => version, ...extra.app }, net: { fetch: request },
      dialog: extra.dialog, shell: extra.shell }
    : id === 'child_process' && extra.spawn
      ? { ...originalRequire(id), spawn: extra.spawn }
      : originalRequire(id)
  mod._compile(result.outputFiles[0].text, filename)
  return mod.exports
}

const channel = load('src/main/updateChannel.ts')
const downloader = load('src/main/updateDownloader.ts')
const taskGuard = load('src/main/activeTaskGuard.ts')
const publisher = require('../scripts/generate-update-channel.cjs')
const { verifyExistingChannel } = require('../scripts/verify-existing-channel.cjs')
const sha256 = 'a'.repeat(64)
const releaseBase = 'https://github.com/naikio0522/Junengpao-Pro/releases/download'

function manifest(version = '0.1.2', platform = 'win32') {
  const name = platform === 'win32'
    ? `巨能跑pro版.Setup.${version}.exe` : `巨能跑pro版.Setup.${version}.dmg`
  return { channel: '0.x', version, asset: {
    url: `${releaseBase}/v${version}/${encodeURIComponent(name)}`,
    sha256, size: 42,
  } }
}

test('0.x channel ignores 1.x, 2.x, prereleases and downgrades', () => {
  assert.deepEqual(channel.parseStableZeroVersion('0.1.1'), [1, 1])
  for (const version of ['2.4.0', '1.5.2', '0.2.0-beta.1', 'v0.01.1', '0.1.1-parity']) {
    assert.equal(channel.parseStableZeroVersion(version), null)
  }
  assert.equal(channel.compareStableZeroVersions('0.1.2', '0.1.1'), 1)
  assert.equal(channel.compareStableZeroVersions('0.1.0', '0.1.1'), -1)
  assert.equal(channel.compareStableZeroVersions('2.4.0', '0.1.1'), null)
  assert.equal(channel.channelManifestUrl('win32'), `${releaseBase}/update-channel-0/windows.json`)
})

test('check update reports unpublished 0.x channel on 404 and never checks from an old 2.x build', async () => {
  let requestedUrl = ''
  const request = async url => {
    requestedUrl = String(url)
    return new Response(null, { status: 404 })
  }
  const currentUpdater = loadUpdaterWithVersion('0.1.2', request)
  const missingChannel = await currentUpdater.checkForUpdate()
  assert.equal(missingChannel.status, 'unpublished')
  assert.match(missingChannel.message, /0.x 更新通道尚未发布/)
  const requestedManifest = new URL(requestedUrl)
  const platformManifest = process.platform === 'darwin' ? 'macos.json' : 'windows.json'
  assert.equal(`${requestedManifest.origin}${requestedManifest.pathname}`,
    `${releaseBase}/update-channel-0/${platformManifest}`)

  requestedUrl = ''
  const oldUpdater = loadUpdaterWithVersion('2.4.2', request)
  assert.equal((await oldUpdater.checkForUpdate()).status, 'unsupported')
  assert.equal(requestedUrl, '')
})

test('network failure names the 0.x channel and offers retry guidance', async () => {
  const updater = loadUpdaterWithVersion('0.1.2', async () => { throw new TypeError('fetch failed') })
  await assert.rejects(() => updater.checkForUpdate(), /无法连接 0.x 更新通道，请检查网络后重试/)
})

test('main-process task query fails closed on active work, HTTP errors, and invalid replies', async () => {
  const ok = async (_url, options) => {
    assert.equal(options.headers['X-VideoMatrix-Token'], 'secret')
    return new Response(JSON.stringify({ count: 0 }))
  }
  assert.equal(await taskGuard.getActiveTaskCount(8765, 'secret', ok), 0)
  await taskGuard.ensureNoActiveTasks(8765, 'secret', ok)
  await assert.rejects(() => taskGuard.ensureNoActiveTasks(8765, 'secret',
    async () => new Response(JSON.stringify({ count: 2 }))), /还有 2 项任务/)
  for (const request of [
    async () => { throw new Error('offline') },
    async () => new Response('unavailable', { status: 503 }),
    async () => new Response(JSON.stringify({ count: -1 })),
    async () => new Response(JSON.stringify({ count: '0' })),
  ]) await assert.rejects(() => taskGuard.ensureNoActiveTasks(8765, 'secret', request),
    /暂时无法确认任务状态/)
  await assert.rejects(() => taskGuard.ensureNoActiveTasks(0, 'secret', ok),
    /暂时无法确认任务状态/)
})

test('minimum supported version is opt-in and applies only below the published floor', async () => {
  const metadata = { ...manifest('0.1.9'), minimumSupportedVersion: '0.1.4' }
  const request = async () => new Response(JSON.stringify(metadata), {
    headers: { 'Content-Type': 'application/json' },
  })
  assert.equal((await loadUpdaterWithVersion('0.1.3', request).checkForUpdate()).status, 'required')
  assert.equal((await loadUpdaterWithVersion('0.1.4', request).checkForUpdate()).status, 'available')
  assert.equal((await loadUpdaterWithVersion('0.1.9', request).checkForUpdate()).status, 'current')
  const optional = async () => new Response(JSON.stringify(manifest('0.1.9')))
  assert.equal((await loadUpdaterWithVersion('0.1.3', optional).checkForUpdate()).status, 'available')
  for (const response of [new Response('down', { status: 503 }),
    new Response(JSON.stringify({ ...metadata, minimumSupportedVersion: '0.2.0' }))]) {
    const updater = loadUpdaterWithVersion('0.1.3', async () => response)
    await assert.rejects(() => updater.checkForUpdate())
  }
})

test('startup remembers only a validated required policy and clears it after online rollback', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-policy-cache-test-'))
  const required = { ...manifest('0.1.9'), minimumSupportedVersion: '0.1.9' }
  const app = { getPath: () => directory }
  const online = loadUpdaterWithVersion('0.1.8',
    async () => new Response(JSON.stringify(required)), { app })
  const offline = loadUpdaterWithVersion('0.1.8',
    async () => { throw new Error('offline') }, { app })
  const cachePath = path.join(directory, 'required-update-0x.json')
  try {
    await assert.rejects(() => offline.checkForStartupUpdate(), /无法连接/,
      'a first offline launch without validated policy must allow the renderer to fail open')
    assert.equal((await online.checkForStartupUpdate()).status, 'required')
    assert.equal(fs.existsSync(cachePath), true)
    const cached = await offline.checkForStartupUpdate()
    assert.equal(cached.status, 'required')
    assert.equal(cached.usingCachedPolicy, true)
    const serverError = loadUpdaterWithVersion('0.1.8',
      async () => new Response('down', { status: 503 }), { app })
    assert.equal((await serverError.checkForStartupUpdate()).status, 'required')
    const anotherInstalledVersion = loadUpdaterWithVersion('0.1.9',
      async () => { throw new Error('offline') }, { app })
    await assert.rejects(() => anotherInstalledVersion.checkForStartupUpdate(), /无法连接/)
    const rollback = loadUpdaterWithVersion('0.1.8',
      async () => new Response(JSON.stringify(manifest('0.1.9'))), { app })
    assert.equal((await rollback.checkForStartupUpdate()).status, 'available')
    assert.equal(fs.existsSync(cachePath), false)
    await assert.rejects(() => offline.checkForStartupUpdate(), /无法连接/)
    fs.writeFileSync(cachePath, JSON.stringify({ schema: 1, currentVersion: '0.1.8', update: {
      ...required, url: 'https://evil.example/fake.exe',
    } }))
    await assert.rejects(() => offline.checkForStartupUpdate(), /无法连接/,
      'a malformed local cache cannot create a mandatory lockout')
  } finally {
    const absolute = path.resolve(directory)
    if (!absolute.startsWith(path.resolve(os.tmpdir()) + path.sep) ||
        !path.basename(absolute).startsWith('vm-policy-cache-test-')) {
      throw new Error('Refusing to clean an unexpected test directory')
    }
    fs.rmSync(absolute, { recursive: true, force: true })
  }
})

test('update manifest accepts only same-repo, matching 0.x release and checksum', () => {
  assert.equal(channel.validateUpdateManifest(manifest(), 'win32').version, '0.1.2')
  assert.equal(channel.validateUpdateManifest(manifest('0.3.4', 'darwin'), 'darwin').filename,
    '巨能跑pro版.Setup.0.3.4.dmg')
  for (const legacyName of ['视频裂变器.Setup.0.1.2.exe', 'VideoMatrix.Setup.0.1.2.exe']) {
    const legacy = manifest()
    legacy.asset.url = `${releaseBase}/v0.1.2/${encodeURIComponent(legacyName)}`
    assert.equal(channel.validateUpdateManifest(legacy, 'win32').filename, legacyName)
  }
  for (const legacyName of ['视频裂变器.Setup.0.1.2-mac.dmg', 'VideoMatrix.Setup.0.1.2-mac.dmg']) {
    const legacy = manifest('0.1.2', 'darwin')
    legacy.asset.url = `${releaseBase}/v0.1.2/${encodeURIComponent(legacyName)}`
    assert.equal(channel.validateUpdateManifest(legacy, 'darwin').filename, legacyName)
  }
  for (const bad of [
    { ...manifest(), version: '2.4.0' },
    { ...manifest(), asset: { ...manifest().asset, sha256: 'bad' } },
    { ...manifest(), asset: { ...manifest().asset, url: 'https://evil.example/malware.exe' } },
    { ...manifest(), asset: { ...manifest().asset,
      url: 'https://github.com/w2968066/VideoMatrix--/releases/download/v0.1.2/%E5%B7%A8%E8%83%BD%E8%B7%91pro%E7%89%88.Setup.0.1.2.exe' } },
    { ...manifest(), asset: { ...manifest().asset,
      url: `${releaseBase}/v2.4.0/VideoMatrix.Setup.2.4.0.exe` } },
    { ...manifest(), asset: { ...manifest().asset, size: 0 } },
    { ...manifest(), minimumSupportedVersion: '0.1.3' },
    { ...manifest(), minimumSupportedVersion: 'v0.1.1' },
    { ...manifest(), minimumSupportedVersion: '2.0.0' },
    { ...manifest(), minimumSupportedVersion: 42 },
  ]) assert.throws(() => channel.validateUpdateManifest(bad, 'win32'))
})

test('publisher advances each published 0.x platform independently without rollback', () => {
  const release = { tag_name: 'v0.1.2', draft: false, prerelease: false, body: '更新说明', assets: [
    { name: '巨能跑pro版.Setup.0.1.2.exe',
      browser_download_url: `${releaseBase}/v0.1.2/%E5%B7%A8%E8%83%BD%E8%B7%91pro%E7%89%88.Setup.0.1.2.exe`,
      digest: `sha256:${sha256}`, size: 100 },
    { name: '巨能跑pro版.Setup.0.1.2.dmg',
      browser_download_url: `${releaseBase}/v0.1.2/%E5%B7%A8%E8%83%BD%E8%B7%91pro%E7%89%88.Setup.0.1.2.dmg`,
      digest: `sha256:${sha256}`, size: 200 },
  ] }
  const outputs = publisher.makeManifests(release, { windows: '1.1', macos: '1.1' })
  assert.equal(outputs.windows.version, '0.1.2')
  assert.equal(outputs.windows.minimumSupportedVersion, '0.1.2',
    'a new platform installer requires its latest version by default')
  assert.equal(outputs.macos.minimumSupportedVersion, '0.1.2')
  assert.equal(outputs.macos.asset.size, 200)
  assert.deepEqual(publisher.makeManifests(release, { windows: '1.2', macos: '1.2' }), {})
  assert.deepEqual(Object.keys(publisher.makeManifests(release, { windows: '1.3' })), ['macos'])
  assert.throws(() => publisher.makeManifests({ ...release, tag_name: 'v2.4.0' }), /v0.x.y/)
  const windowsOnly = publisher.makeManifests({ ...release, assets: release.assets.slice(0, 1) })
  assert.deepEqual(Object.keys(windowsOnly), ['windows'])
  assert.equal(windowsOnly.windows.minimumSupportedVersion, '0.1.2')
  assert.deepEqual(Object.keys(publisher.makeManifests(
    { ...release, assets: release.assets.slice(0, 1) },
    { windows: '1.1', macos: '1.1' }, { macos: '0.1.1' })), ['windows'],
  'a Windows-only release must leave the macOS channel untouched')
  const legacyRelease = { ...release, assets: release.assets.map((asset, index) => {
    const name = index === 0 ? 'VideoMatrix.Setup.0.1.2.exe' : '视频裂变器.Setup.0.1.2-mac.dmg'
    return { ...asset, name,
      browser_download_url: `${releaseBase}/v0.1.2/${encodeURIComponent(name)}` }
  }) }
  assert.deepEqual(Object.keys(publisher.makeManifests(legacyRelease)), ['windows', 'macos'])
  const oldRepoRelease = { ...release, assets: release.assets.map(asset => ({ ...asset,
    browser_download_url: asset.browser_download_url.replace('naikio0522/Junengpao-Pro', 'w2968066/VideoMatrix--'),
  })) }
  assert.throws(() => publisher.makeManifests(oldRepoRelease), /不在当前 0.x Release/)
  assert.throws(() => publisher.makeManifests({ ...release, assets: [] }), /至少需要一个平台/)
  assert.throws(() => publisher.makeManifests({ ...release, draft: true }), /v0.x.y/)

  const policy = publisher.makeManifests(release, { windows: '1.2', macos: '1.2' }, {},
    { windows: '0.1.1' })
  assert.deepEqual(Object.keys(policy), ['windows'])
  assert.equal(policy.windows.minimumSupportedVersion, '0.1.1')
  const retained = publisher.makeManifests(release, { windows: '1.1' },
    { windows: '0.1.1' })
  assert.equal(retained.windows.minimumSupportedVersion, '0.1.2',
    'a later release tightens the floor unless explicitly overridden')
  assert.deepEqual(publisher.makeManifests(release, { windows: '1.2' },
    { windows: '0.1.1' }), { macos: outputs.macos },
  'republishing the same release without an override preserves the previous policy')
  const disabled = publisher.makeManifests(release, { windows: '1.2' },
    { windows: '0.1.1' }, { windows: 'none' })
  assert.equal(disabled.windows.minimumSupportedVersion, undefined)
  assert.throws(() => publisher.makeManifests(release, { windows: '1.3' },
    { windows: '0.1.1' }, { windows: '0.1.2' }), /早于当前通道/,
  'an older release cannot change policy or roll back the Windows channel')
  assert.throws(() => publisher.makeManifests(
    { ...release, assets: release.assets.slice(0, 1) }, {}, {},
    { macos: '0.1.1' }), /没有安装包/)
  assert.throws(() => publisher.makeManifests(release, {}, {},
    { windows: '0.1.3' }), /最低支持版本/)
})

test('existing update channel must be completely downloaded before a prior release can be republished', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-channel-test-'))
  const release = { tag_name: 'update-channel-0', assets: [
    { name: 'windows.json' }, { name: 'macos.json' },
  ] }
  try {
    assert.deepEqual(verifyExistingChannel(release), ['windows.json', 'macos.json'])
    assert.throws(() => verifyExistingChannel(release, directory), /not downloaded/)
    fs.writeFileSync(path.join(directory, 'windows.json'), JSON.stringify({ channel: '0.x', version: '0.1.2' }))
    assert.throws(() => verifyExistingChannel(release, directory), /macos.json/)
    fs.writeFileSync(path.join(directory, 'macos.json'), JSON.stringify({ channel: '0.x', version: '0.1.3' }))
    assert.deepEqual(verifyExistingChannel(release, directory), ['windows.json', 'macos.json'])
    fs.writeFileSync(path.join(directory, 'windows.json'), JSON.stringify({
      channel: '0.x', version: '0.1.2', minimumSupportedVersion: '0.1.1',
    }))
    assert.deepEqual(verifyExistingChannel(release, directory), ['windows.json', 'macos.json'])
    fs.writeFileSync(path.join(directory, 'windows.json'), JSON.stringify({
      channel: '0.x', version: '0.1.2', minimumSupportedVersion: '0.1.3',
    }))
    assert.throws(() => verifyExistingChannel(release, directory), /minimum supported/)
    assert.throws(() => verifyExistingChannel({ ...release, assets: [] }), /no manifest/)
    assert.throws(() => verifyExistingChannel({ ...release, tag_name: 'v2.0.0' }), /invalid/)
  } finally {
    const absolute = path.resolve(directory)
    if (!absolute.startsWith(path.resolve(os.tmpdir()) + path.sep) || !path.basename(absolute).startsWith('vm-channel-test-')) {
      throw new Error('Refusing to clean an unexpected test directory')
    }
    fs.rmSync(absolute, { recursive: true, force: true })
  }
})

test('installer rechecks tasks after download and never launches while work is active',
  { skip: process.platform !== 'win32' }, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-install-gate-test-'))
    const payload = Buffer.from('installer bytes for gate test')
    const metadata = manifest('0.1.9')
    metadata.minimumSupportedVersion = '0.1.9'
    metadata.asset.sha256 = createHash('sha256').update(payload).digest('hex')
    metadata.asset.size = payload.length
    const request = async url => String(url).includes('windows.json')
      ? new Response(JSON.stringify(metadata))
      : new Response(payload, { headers: { 'content-length': String(payload.length) } })
    let launches = 0
    let closes = 0
    const spawn = () => {
      launches++
      const child = new EventEmitter()
      child.unref = () => {}
      process.nextTick(() => child.emit('spawn'))
      return child
    }
    const updater = loadUpdaterWithVersion('0.1.8', request, {
      app: { isPackaged: true, getPath: () => directory },
      dialog: { showMessageBox: async () => ({ response: 1 }) },
      spawn,
    })
    try {
      await assert.rejects(() => updater.downloadAndInstallUpdate({}, () => {},
        async () => { closes++ }, async () => { throw new Error('仍有任务运行') }), /仍有任务运行/)
      assert.equal(launches, 0)
      assert.equal(closes, 0)
      assert.equal(await updater.downloadAndInstallUpdate({}, () => {},
        async () => { closes++ }, async () => {}), 'installer-launched')
      assert.equal(launches, 1)
      assert.equal(closes, 1)
    } finally {
      const absolute = path.resolve(directory)
      if (!absolute.startsWith(path.resolve(os.tmpdir()) + path.sep) ||
          !path.basename(absolute).startsWith('vm-install-gate-test-')) {
        throw new Error('Refusing to clean an unexpected test directory')
      }
      fs.rmSync(absolute, { recursive: true, force: true })
    }
  })

test('update download streams and verifies the release SHA-256 before creating installer', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-updater-test-'))
  const payload = Buffer.from('verified installer test payload')
  const checksum = createHash('sha256').update(payload).digest('hex')
  const metadata = manifest()
  metadata.asset.sha256 = checksum
  metadata.asset.size = payload.length
  const update = channel.validateUpdateManifest(metadata, 'win32')
  const progress = []
  const fakeFetch = async () => new Response(payload, {
    status: 200, headers: { 'content-length': String(payload.length) },
  })
  try {
    const installer = await downloader.downloadVerifiedAsset(update, 'win32', directory,
      event => progress.push(event.percent), fakeFetch)
    assert.deepEqual(fs.readFileSync(installer), payload)
    assert.equal(progress.at(-1), 100)
    await assert.rejects(() => downloader.downloadVerifiedAsset({ ...update, sha256: '0'.repeat(64) },
      'win32', directory, () => {}, fakeFetch), /SHA-256/)
    assert.equal(fs.readdirSync(directory).some(name => name.endsWith('.part')), false)
  } finally {
    const absolute = path.resolve(directory)
    if (!absolute.startsWith(path.resolve(os.tmpdir()) + path.sep) || !path.basename(absolute).startsWith('vm-updater-test-')) {
      throw new Error('Refusing to clean an unexpected test directory')
    }
    fs.rmSync(absolute, { recursive: true, force: true })
  }
})
