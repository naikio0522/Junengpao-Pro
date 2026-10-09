#!/usr/bin/env node
// A channel that already exists must be completely readable before publishing
// another manifest. Treating a transient download failure as an empty channel
// could roll a platform back to an older release.
const fs = require('node:fs')
const path = require('node:path')

const allowedNames = ['windows.json', 'macos.json']

function verifyExistingChannel(release, directory) {
  if (release?.tag_name !== 'update-channel-0' || !Array.isArray(release.assets)) {
    throw new Error('Existing update channel release metadata is invalid')
  }
  const names = release.assets.map(asset => asset.name).filter(name => allowedNames.includes(name))
  if (names.length === 0 || new Set(names).size !== names.length) {
    throw new Error('Existing update channel has no manifest or duplicate manifests')
  }
  if (!directory) return names

  for (const name of names) {
    const filename = path.join(directory, name)
    if (!fs.existsSync(filename) || fs.statSync(filename).size === 0) {
      throw new Error(`Existing channel manifest was not downloaded: ${name}`)
    }
    const manifest = JSON.parse(fs.readFileSync(filename, 'utf8'))
    const parseVersion = value => {
      if (typeof value !== 'string' || !/^0\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)) return null
      const parts = value.split('.').map(Number)
      return parts.every(Number.isSafeInteger) ? parts : null
    }
    const version = parseVersion(manifest.version)
    if (manifest.channel !== '0.x' || !version) {
      throw new Error(`Existing channel manifest is invalid: ${name}`)
    }
    if (manifest.minimumSupportedVersion !== undefined) {
      const minimum = parseVersion(manifest.minimumSupportedVersion)
      if (!minimum || minimum[1] > version[1] ||
          (minimum[1] === version[1] && minimum[2] > version[2])) {
        throw new Error(`Existing channel minimum supported version is invalid: ${name}`)
      }
    }
  }
  return names
}

if (require.main === module) {
  const [releaseJson, directory] = process.argv.slice(2)
  if (!releaseJson) {
    console.error('Usage: node verify-existing-channel.cjs <channel-release.json> [downloaded-dir]')
    process.exit(2)
  }
  verifyExistingChannel(JSON.parse(fs.readFileSync(releaseJson, 'utf8')), directory)
}

module.exports = { verifyExistingChannel }
