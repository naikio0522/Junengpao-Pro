#!/usr/bin/env node
// Run after at least one platform installer exists on a published v0.x release.
const fs = require('node:fs')
const path = require('node:path')

function parseVersion(tag) {
  const match = /^v0\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(tag || '')
  return match && Number.isSafeInteger(Number(match[1])) && Number.isSafeInteger(Number(match[2]))
    ? `${match[1]}.${match[2]}` : null
}

function compareVersions(a, b) {
  const left = a.split('.').map(Number)
  const right = b.split('.').map(Number)
  return left[0] - right[0] || left[1] - right[1]
}

function resolveMinimumSupportedVersion(requested, version, platform) {
  const minimum = requested === 'none' ? undefined : requested
  if (minimum !== undefined &&
      (!/^0\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(minimum) ||
        !parseVersion(`v${minimum}`) || compareVersions(minimum.slice(2), version) > 0)) {
    throw new Error(`${platform} 最低支持版本必须是当前发布版本以内的 0.x.y，或填写 none 关闭策略`)
  }
  return minimum
}

function assetFor(release, version, platform) {
  const expected = platform === 'windows'
    ? [`巨能跑pro版.Setup.0.${version}.exe`, `视频裂变器.Setup.0.${version}.exe`, `VideoMatrix.Setup.0.${version}.exe`]
    : [`巨能跑pro版.Setup.0.${version}.dmg`, `巨能跑pro版.Setup.0.${version}-mac.dmg`,
      `视频裂变器.Setup.0.${version}-mac.dmg`, `VideoMatrix.Setup.0.${version}-mac.dmg`]
  const asset = release.assets.find(item => expected.includes(item.name))
  if (!asset) return null
  if (!/^sha256:[0-9a-f]{64}$/i.test(asset.digest || '') ||
      !Number.isSafeInteger(asset.size) || asset.size < 1 || asset.size > 3_000_000_000) {
    throw new Error(`${platform} 安装包大小无效或没有 GitHub SHA-256 digest`)
  }
  const expectedUrl = `https://github.com/naikio0522/Junengpao-Pro/releases/download/v0.${version}/${encodeURIComponent(asset.name)}`
  if (asset.browser_download_url !== expectedUrl &&
      asset.browser_download_url !== decodeURI(expectedUrl)) {
    throw new Error(`${platform} 安装包不在当前 0.x Release 中`)
  }
  return {
    url: asset.browser_download_url,
    sha256: asset.digest.slice('sha256:'.length).toLowerCase(),
    size: asset.size,
  }
}

function makeManifests(release, existingVersions = {}, previousMinimums = {}, policyOverrides = {}) {
  const version = parseVersion(release.tag_name)
  if (!version || release.draft || release.prerelease || !Array.isArray(release.assets)) {
    throw new Error('只允许已公开发布、非预发布的 v0.x.y Release 更新 0.x 通道')
  }
  const notes = typeof release.body === 'string' ? release.body.slice(0, 2000) : ''
  const outputs = {}
  let assetCount = 0
  for (const platform of ['windows', 'macos']) {
    const asset = assetFor(release, version, platform)
    if (!asset) {
      if (policyOverrides[platform] !== undefined) {
        throw new Error(`${platform} 的目标 Release 没有安装包，不能调整该平台的最低支持版本`)
      }
      continue
    }
    assetCount += 1
    const previousVersion = existingVersions[platform]
    const comparison = previousVersion ? compareVersions(version, previousVersion) : 1
    const requestedMinimum = policyOverrides[platform] === undefined
      ? comparison === 0 ? previousMinimums[platform] : `0.${version}`
      : policyOverrides[platform]
    const minimumSupportedVersion = resolveMinimumSupportedVersion(
      requestedMinimum, version, platform)
    if (previousVersion) {
      if (comparison < 0 && policyOverrides[platform] !== undefined) {
        throw new Error(`${platform} 的目标 Release 早于当前通道，不能用旧版修改最低支持版本`)
      }
      if (comparison < 0 || (comparison === 0 &&
          minimumSupportedVersion === previousMinimums[platform])) continue
    }
    outputs[platform] = {
      channel: '0.x', version: `0.${version}`, notes, asset,
      ...(minimumSupportedVersion === undefined ? {} : { minimumSupportedVersion }),
    }
  }
  if (!assetCount) throw new Error('此 0.x Release 至少需要一个平台的正式安装包')
  return outputs
}

if (require.main === module) {
  const [releaseJson, outputDirectory, oldDirectory] = process.argv.slice(2)
  if (!releaseJson || !outputDirectory) {
    console.error('Usage: node generate-update-channel.cjs <release.json> <output-dir> [previous-channel-dir]')
    process.exit(2)
  }
  const release = JSON.parse(fs.readFileSync(releaseJson, 'utf8'))
  const existingVersions = {}
  const previousMinimums = {}
  const policyOverrides = {}
  for (const platform of ['windows', 'macos']) {
    const oldManifest = oldDirectory && path.join(oldDirectory, `${platform}.json`)
    if (oldManifest && fs.existsSync(oldManifest)) {
      const previous = JSON.parse(fs.readFileSync(oldManifest, 'utf8'))
      const previousVersion = parseVersion(`v${previous.version}`)
      if (previous.channel !== '0.x' || !previousVersion) {
        throw new Error(`已有 ${platform} 0.x 通道元数据无效；拒绝覆盖`)
      }
      existingVersions[platform] = previousVersion
      if (previous.minimumSupportedVersion !== undefined) {
        previousMinimums[platform] = resolveMinimumSupportedVersion(
          previous.minimumSupportedVersion, existingVersions[platform], platform)
      }
    }
    const override = process.env[platform === 'windows'
      ? 'JNP_MINIMUM_WINDOWS_VERSION' : 'JNP_MINIMUM_MACOS_VERSION']
    if (override) policyOverrides[platform] = override.trim()
  }
  const manifests = makeManifests(release, existingVersions, previousMinimums, policyOverrides)
  fs.mkdirSync(outputDirectory, { recursive: true })
  for (const [platform, manifest] of Object.entries(manifests)) {
    fs.writeFileSync(path.join(outputDirectory, `${platform}.json`), `${JSON.stringify(manifest, null, 2)}\n`)
  }
}

module.exports = { parseVersion, makeManifests }
