const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const os = require('node:os')
const { createHash } = require('node:crypto')
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

function loadUpdaterWithVersion(version, request) {
  const filename = path.resolve(__dirname, '../src/main/releaseUpdater.ts')
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true,
    platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module)
  mod.paths = Module._nodeModulePaths(path.dirname(filename))
  const originalRequire = mod.require.bind(mod)
  mod.require = id => id === 'electron'
    ? { app: { getVersion: () => version }, net: { fetch: request } }
    : originalRequire(id)
  mod._compile(result.outputFiles[0].text, filename)
  return mod.exports
}

const channel = load('src/main/updateChannel.ts')
const downloader = load('src/main/updateDownloader.ts')
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
  assert.equal(`${requestedManifest.origin}${requestedManifest.pathname}`,
    `${releaseBase}/update-channel-0/windows.json`)

  requestedUrl = ''
  const oldUpdater = loadUpdaterWithVersion('2.4.2', request)
  assert.equal((await oldUpdater.checkForUpdate()).status, 'unsupported')
  assert.equal(requestedUrl, '')
})

test('network failure names the 0.x channel and offers retry guidance', async () => {
  const updater = loadUpdaterWithVersion('0.1.2', async () => { throw new TypeError('fetch failed') })
  await assert.rejects(() => updater.checkForUpdate(), /无法连接 0.x 更新通道，请检查网络后重试/)
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
  assert.equal(outputs.macos.asset.size, 200)
  assert.deepEqual(publisher.makeManifests(release, { windows: '1.2', macos: '1.2' }), {})
  assert.deepEqual(Object.keys(publisher.makeManifests(release, { windows: '1.3' })), ['macos'])
  assert.throws(() => publisher.makeManifests({ ...release, tag_name: 'v2.4.0' }), /v0.x.y/)
  const windowsOnly = publisher.makeManifests({ ...release, assets: release.assets.slice(0, 1) })
  assert.deepEqual(Object.keys(windowsOnly), ['windows'])
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
