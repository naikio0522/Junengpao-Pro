import { createHash, randomUUID } from 'crypto'
import fs from 'fs'
import path from 'path'
import { Readable, Transform } from 'stream'
import { pipeline } from 'stream/promises'
import { AvailableUpdate, UpdatePlatform, validateUpdateManifest } from './updateChannel'

export interface UpdateDownloadProgress {
  received: number
  total: number
  percent: number
}

/** Streams a release asset to a unique temp file, then checks exact size + SHA-256. */
export async function downloadVerifiedAsset(
  update: AvailableUpdate,
  platform: UpdatePlatform,
  directory: string,
  onProgress: (progress: UpdateDownloadProgress) => void,
  fetchAsset: typeof fetch = fetch,
): Promise<string> {
  // This guard is repeated at the install boundary; renderer IPC never supplies
  // a URL and the published manifest cannot redirect us to an old 2.x release.
  validateUpdateManifest({ channel: '0.x', version: update.version,
    asset: { url: update.url, sha256: update.sha256, size: update.size } }, platform)
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  const destination = path.join(directory, `${randomUUID()}-${update.filename}`)
  const partial = `${destination}.part`
  let received = 0
  let lastPercent = -1
  const hash = createHash('sha256')
  try {
    const response = await fetchAsset(update.url, {
      headers: { 'User-Agent': 'VideoMatrix-0x-Updater' },
      signal: AbortSignal.timeout(30 * 60 * 1000),
    })
    if (!response.ok || !response.body) throw new Error(`更新包下载失败：服务器返回 ${response.status}。`)
    const declaredLength = Number(response.headers.get('content-length'))
    if (Number.isFinite(declaredLength) && declaredLength > 0 && declaredLength !== update.size) {
      throw new Error('更新包大小与发布信息不一致，已停止安装。')
    }
    onProgress({ received: 0, total: update.size, percent: 0 })
    const meter = new Transform({
      transform(chunk: Buffer, _encoding, callback) {
        received += chunk.length
        if (received > update.size) return callback(new Error('更新包超过发布大小，已停止安装。'))
        hash.update(chunk)
        const percent = Math.min(100, Math.floor(received / update.size * 100))
        if (percent !== lastPercent) {
          lastPercent = percent
          onProgress({ received, total: update.size, percent })
        }
        callback(null, chunk)
      },
    })
    await pipeline(Readable.fromWeb(response.body as any), meter, fs.createWriteStream(partial, { flags: 'wx' }))
    if (received !== update.size || hash.digest('hex') !== update.sha256) {
      throw new Error('更新包 SHA-256 校验失败，已停止安装。')
    }
    fs.renameSync(partial, destination)
    onProgress({ received, total: update.size, percent: 100 })
    return destination
  } catch (error) {
    try { fs.unlinkSync(partial) } catch { /* No partial file, or already moved. */ }
    throw error
  }
}
