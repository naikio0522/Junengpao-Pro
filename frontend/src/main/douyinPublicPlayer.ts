import { BrowserWindow, session } from 'electron'

type MediaAddress = {
  url_list: string[]
  width?: number
  height?: number
  data_size?: number
}

export type PublicDouyinDetail = {
  aweme_id: string
  desc?: string
  duration?: number
  video: {
    duration?: number
    cover?: { url_list: string[] }
    play_addr?: MediaAddress
    play_addr_h264?: MediaAddress
    play_addr_265?: MediaAddress
    play_addr_bytevc1?: MediaAddress
    bit_rate?: Array<{ gear_name?: string; play_addr: MediaAddress }>
  }
}

const DETAIL_PATH = '/aweme/v1/web/aweme/detail/'
const PLAYER_TIMEOUT_MS = 25_000
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024
const MEDIA_SUFFIXES = [
  'douyinvod.com', 'douyin.com', 'snssdk.com', 'byteimg.com',
  'douyinpic.com', 'pstatp.com', 'ibytedtos.com', 'bytedance.com',
]
const inFlight = new Map<string, Promise<PublicDouyinDetail>>()
let guestSessionConfigured = false

function isPlatformMediaUrl(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 4096) return false
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password &&
      (!url.port || url.port === '443') && !url.hash &&
      !url.pathname.toLowerCase().includes('/playwm') &&
      MEDIA_SUFFIXES.some(suffix => url.hostname === suffix || url.hostname.endsWith(`.${suffix}`))
  } catch {
    return false
  }
}

function mediaAddress(raw: unknown): MediaAddress | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const source = raw as Record<string, unknown>
  const urls = Array.isArray(source.url_list)
    ? source.url_list.slice(0, 8).filter(isPlatformMediaUrl) : []
  if (!urls.length) return undefined
  const result: MediaAddress = { url_list: urls }
  for (const key of ['width', 'height', 'data_size'] as const) {
    const value = source[key]
    if (typeof value === 'number' && Number.isSafeInteger(value) && value > 0) {
      result[key] = value
    }
  }
  return result
}

export function parsePublicPlayerDetail(videoId: string, body: string): PublicDouyinDetail {
  if (Buffer.byteLength(body) > MAX_RESPONSE_BYTES) throw new Error('官方播放器响应过大')
  let response: Record<string, unknown>
  try {
    response = JSON.parse(body) as Record<string, unknown>
  } catch {
    throw new Error('官方播放器未返回有效视频数据')
  }
  const raw = response.aweme_detail
  if (!raw || typeof raw !== 'object') throw new Error('官方播放器未提供公开视频详情')
  const detail = raw as Record<string, unknown>
  if (String(detail.aweme_id) !== videoId) throw new Error('播放器返回的视频与链接不一致')
  if (!detail.video || typeof detail.video !== 'object') {
    throw new Error('官方播放器未提供视频流')
  }
  const source = detail.video as Record<string, unknown>
  const video: PublicDouyinDetail['video'] = {}
  for (const key of ['play_addr', 'play_addr_h264', 'play_addr_265',
    'play_addr_bytevc1'] as const) {
    const address = mediaAddress(source[key])
    if (address) video[key] = address
  }
  if (Array.isArray(source.bit_rate)) {
    video.bit_rate = source.bit_rate.slice(0, 24).flatMap((item: unknown) => {
      if (!item || typeof item !== 'object') return []
      const bitrate = item as Record<string, unknown>
      const address = mediaAddress(bitrate.play_addr)
      if (!address) return []
      return [{
        gear_name: typeof bitrate.gear_name === 'string'
          ? bitrate.gear_name.slice(0, 64) : undefined,
        play_addr: address,
      }]
    })
  }
  const cover = source.cover as Record<string, unknown> | undefined
  if (cover && Array.isArray(cover.url_list)) {
    video.cover = { url_list: cover.url_list.slice(0, 3).filter(isPlatformMediaUrl) }
  }
  if (typeof source.duration === 'number' && Number.isFinite(source.duration)) {
    video.duration = source.duration
  }
  if (!video.play_addr && !video.play_addr_h264 && !video.play_addr_265 &&
      !video.play_addr_bytevc1 && !video.bit_rate?.length) {
    throw new Error('官方播放器未提供可下载的直接视频流')
  }
  return {
    aweme_id: videoId,
    desc: typeof detail.desc === 'string' ? detail.desc.slice(0, 120) : undefined,
    duration: typeof detail.duration === 'number' && Number.isFinite(detail.duration)
      ? detail.duration : undefined,
    video,
  }
}

function isExpectedDetailRequest(rawUrl: string, videoId: string): boolean {
  try {
    const url = new URL(rawUrl)
    return url.protocol === 'https:' && url.hostname === 'www.douyin.com' &&
      url.pathname === DETAIL_PATH && url.searchParams.get('aweme_id') === videoId
  } catch {
    return false
  }
}

async function capture(videoId: string): Promise<PublicDouyinDetail> {
  const playerUrl = `https://open.douyin.com/player/video?vid=${videoId}&autoplay=0`
  const guestSession = session.fromPartition('douyin-public-guest')
  if (!guestSessionConfigured) {
    guestSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
    guestSession.on('will-download', event => event.preventDefault())
    guestSessionConfigured = true
  }
  const window = new BrowserWindow({
    show: false, width: 640, height: 480,
    webPreferences: {
      partition: 'douyin-public-guest', nodeIntegration: false,
      contextIsolation: true, sandbox: true, webSecurity: true,
      backgroundThrottling: false,
    },
  })
  const contents = window.webContents
  const debuggerApi = contents.debugger
  const detailRequests = new Set<string>()
  let timer: ReturnType<typeof setTimeout> | undefined
  const deadline = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => reject(new Error('官方播放器解析超时，请稍后重试')),
      PLAYER_TIMEOUT_MS)
  })
  const run = async (): Promise<PublicDouyinDetail> => {
    // Electron starts the renderer process on first navigation. Attach CDP
    // after a local blank page so Network.enable can complete before the
    // official player begins loading.
    await contents.loadURL('about:blank')
    contents.setWindowOpenHandler(() => ({ action: 'deny' }))
    contents.on('will-attach-webview', event => event.preventDefault())
    contents.on('will-navigate', (event, target) => {
      if (target !== playerUrl) event.preventDefault()
    })
    contents.on('will-redirect', (event, target) => {
      if (target !== playerUrl) event.preventDefault()
    })
    debuggerApi.attach('1.3')
    await debuggerApi.sendCommand('Network.enable', {
      maxTotalBufferSize: MAX_RESPONSE_BYTES * 2,
      maxResourceBufferSize: MAX_RESPONSE_BYTES,
    })
    const result = new Promise<PublicDouyinDetail>((resolve, reject) => {
      debuggerApi.on('message', (_event, method, params) => {
        if (method === 'Network.responseReceived' &&
            isExpectedDetailRequest(String(params.response?.url || ''), videoId)) {
          if (params.response?.status !== 200) {
            reject(new Error('官方播放器暂时无法读取此公开视频'))
          } else if (params.response?.mimeType &&
                     !String(params.response.mimeType).includes('json')) {
            reject(new Error('官方播放器未返回视频数据'))
          } else {
            detailRequests.add(String(params.requestId))
          }
        }
        if (method === 'Network.loadingFinished' &&
            detailRequests.delete(String(params.requestId))) {
          void debuggerApi.sendCommand('Network.getResponseBody', {
            requestId: params.requestId,
          }).then(payload => {
            const raw = String(payload.body || '')
            if (raw.length > (payload.base64Encoded ? MAX_RESPONSE_BYTES * 2 : MAX_RESPONSE_BYTES)) {
              throw new Error('官方播放器响应过大')
            }
            const body = payload.base64Encoded ? Buffer.from(raw, 'base64').toString('utf8') : raw
            resolve(parsePublicPlayerDetail(videoId, body))
          }).catch(reject)
        }
      })
      contents.once('destroyed', () => reject(new Error('官方播放器窗口已关闭')))
      void contents.loadURL(playerUrl).catch(() => reject(new Error('官方播放器连接失败')))
    })
    return await result
  }
  try {
    return await Promise.race([run(), deadline])
  } finally {
    if (timer) clearTimeout(timer)
    try {
      if (debuggerApi.isAttached()) debuggerApi.detach()
    } catch { /* the hidden window may already have closed */ }
    if (!window.isDestroyed()) window.destroy()
  }
}

export function resolvePublicDouyinPlayer(videoId: string): Promise<PublicDouyinDetail> {
  if (!/^\d{10,24}$/.test(videoId)) {
    return Promise.reject(new Error('只支持公开的抖音单条视频链接'))
  }
  const existing = inFlight.get(videoId)
  if (existing) return existing
  if (inFlight.size) return Promise.reject(new Error('已有抖音链接正在解析，请稍后重试'))
  const pending = capture(videoId).finally(() => inFlight.delete(videoId))
  inFlight.set(videoId, pending)
  return pending
}
