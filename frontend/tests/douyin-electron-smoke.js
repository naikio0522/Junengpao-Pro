// Optional live smoke: build douyinPublicPlayer.ts into dist/main first, then
// run `electron tests/douyin-electron-smoke.js <public-video-id>`.
const { app } = require('electron')
const os = require('os')
const path = require('path')
const { resolvePublicDouyinPlayer } = require('../dist/main/douyinPublicPlayer.cjs')

const videoId = process.argv[2]
if (!/^\d{10,24}$/.test(videoId || '')) {
  throw new Error('Pass a public numeric Douyin video ID')
}
app.setPath('userData', path.join(os.tmpdir(), 'jnp-douyin-public-smoke'))
app.disableHardwareAcceleration()
app.on('window-all-closed', () => {})

app.whenReady().then(async () => {
  try {
    const detail = await resolvePublicDouyinPlayer(videoId)
    const mediaUrl = detail.video.play_addr?.url_list[0] ||
      detail.video.bit_rate?.[0]?.play_addr.url_list[0] || ''
    const url = new URL(mediaUrl)
    const result = {
      videoId: detail.aweme_id,
      mediaHost: url.hostname,
      playAddrCount: detail.video.play_addr?.url_list.length || 0,
    }
    if (process.env.JNP_DOUYIN_SMOKE_MEDIA === '1') {
      const response = await fetch(mediaUrl, {
        headers: {
          Range: 'bytes=0-31',
          Referer: `https://open.douyin.com/player/video?vid=${videoId}`,
        },
      })
      const reader = response.body?.getReader()
      const first = await reader?.read()
      await reader?.cancel()
      if (!response.ok || !response.headers.get('content-type')?.startsWith('video/') ||
          !first?.value || !Buffer.from(first.value).subarray(4, 8).equals(Buffer.from('ftyp'))) {
        throw new Error('Public playback URL did not return MP4 bytes')
      }
      result.mediaStatus = response.status
    }
    console.log(JSON.stringify(result))
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error))
    process.exitCode = 1
  } finally {
    app.quit()
  }
})
