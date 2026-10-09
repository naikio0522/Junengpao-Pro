/** The 0.x desktop line deliberately never follows GitHub's `latest` 2.x release. */
export const UPDATE_CHANNEL_TAG = 'update-channel-0'
export const UPDATE_REPOSITORY = 'naikio0522/Junengpao-Pro'

export type UpdatePlatform = 'win32' | 'darwin'

export interface UpdateAsset {
  url: string
  sha256: string
  size: number
}

export interface UpdateManifest {
  channel: '0.x'
  version: string
  /** Optional policy for clients that support mandatory updates. */
  minimumSupportedVersion?: string
  notes?: string
  asset: UpdateAsset
}

export interface AvailableUpdate {
  version: string
  minimumSupportedVersion?: string
  notes: string
  url: string
  sha256: string
  size: number
  filename: string
  releasePage: string
}

export function parseStableZeroVersion(value: unknown): [number, number] | null {
  if (typeof value !== 'string') return null
  const match = /^v?0\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(value)
  if (!match) return null
  const minor = Number(match[1])
  const patch = Number(match[2])
  return Number.isSafeInteger(minor) && Number.isSafeInteger(patch) ? [minor, patch] : null
}

export function compareStableZeroVersions(a: string, b: string): number | null {
  const left = parseStableZeroVersion(a)
  const right = parseStableZeroVersion(b)
  if (!left || !right) return null
  return left[0] - right[0] || left[1] - right[1]
}

export function channelManifestUrl(platform: UpdatePlatform): string {
  const name = platform === 'win32' ? 'windows.json' : 'macos.json'
  return `https://github.com/${UPDATE_REPOSITORY}/releases/download/${UPDATE_CHANNEL_TAG}/${name}`
}

export function validateUpdateManifest(value: unknown, platform: UpdatePlatform): AvailableUpdate {
  const data = value as Partial<UpdateManifest> | null
  if (!data || data.channel !== '0.x' || !parseStableZeroVersion(data.version)) {
    throw new Error('0.x 更新通道返回了无效版本。')
  }
  const version = data.version!.replace(/^v/, '')
  const minimumSupportedVersion = data.minimumSupportedVersion
  if (minimumSupportedVersion !== undefined &&
      (typeof minimumSupportedVersion !== 'string' ||
        !/^0\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(minimumSupportedVersion) ||
        !parseStableZeroVersion(minimumSupportedVersion) ||
        compareStableZeroVersions(minimumSupportedVersion, version)! > 0)) {
    throw new Error('0.x 更新通道返回了无效的最低支持版本。')
  }
  const asset = data.asset
  if (!asset || !/^[0-9a-f]{64}$/i.test(asset.sha256 || '') ||
      !Number.isSafeInteger(asset.size) || asset.size < 1 || asset.size > 3_000_000_000) {
    throw new Error('更新包缺少有效的 SHA-256 或文件大小。')
  }
  let url: URL
  try { url = new URL(asset.url) } catch { throw new Error('更新包下载地址无效。') }
  let components: string[]
  try { components = url.pathname.split('/').map(decodeURIComponent) } catch {
    throw new Error('更新包下载地址编码无效。')
  }
  const prefix = ['', ...UPDATE_REPOSITORY.split('/'), 'releases', 'download', `v${version}`]
  const filename = components[6] || ''
  const acceptableNames = platform === 'win32'
    ? [`巨能跑pro版.Setup.${version}.exe`, `视频裂变器.Setup.${version}.exe`, `VideoMatrix.Setup.${version}.exe`]
    : [`巨能跑pro版.Setup.${version}.dmg`, `巨能跑pro版.Setup.${version}-mac.dmg`,
      `视频裂变器.Setup.${version}-mac.dmg`, `VideoMatrix.Setup.${version}-mac.dmg`]
  if (url.protocol !== 'https:' || url.hostname !== 'github.com' ||
      url.username || url.password || url.search || url.hash ||
      components.length !== 7 || prefix.some((part, index) => components[index] !== part) ||
      !acceptableNames.includes(filename)) {
    throw new Error('更新包不在此软件的 0.x 官方发布地址。')
  }
  return {
    version,
    minimumSupportedVersion,
    notes: typeof data.notes === 'string' ? data.notes.slice(0, 2000) : '',
    url: url.toString(),
    sha256: asset.sha256.toLowerCase(),
    size: asset.size,
    filename,
    releasePage: `https://github.com/${UPDATE_REPOSITORY}/releases/tag/v${version}`,
  }
}
