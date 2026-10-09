const assert = require('node:assert/strict')
const fs = require('node:fs')
const Module = require('node:module')
const path = require('node:path')
const test = require('node:test')
const esbuild = require('esbuild')

const source = path.resolve(__dirname, '../src/main/douyinPublicPlayer.ts')
const compiled = esbuild.transformSync(fs.readFileSync(source, 'utf8'), {
  loader: 'ts', format: 'cjs', target: 'node18',
}).code
const loaded = new Module(source, module)
loaded.filename = source
loaded.paths = Module._nodeModulePaths(path.dirname(source))
loaded._compile(compiled, source)
const { parsePublicPlayerDetail } = loaded.exports

test('official player payload keeps only direct platform playback addresses', () => {
  const detail = parsePublicPlayerDetail('1234567890123456789', JSON.stringify({
    aweme_detail: {
      aweme_id: '1234567890123456789', desc: '公开测试', duration: 10000,
      video: {
        play_addr: { url_list: [
          'http://v26.douyinvod.com/unsafe.mp4',
          'https://127.0.0.1/private.mp4',
          'https://www.douyin.com/aweme/v1/playwm/?video_id=bad',
          'https://v26.douyinvod.com/play.mp4',
        ] },
        download_addr: { url_list: ['https://v26.douyinvod.com/playwm.mp4'] },
        bit_rate: [{ play_addr: { url_list: ['https://douyinvod.com.evil.test/bad.mp4'] } }],
      },
    },
  }))
  assert.deepEqual(detail.video.play_addr.url_list,
    ['https://v26.douyinvod.com/play.mp4'])
  assert.equal(detail.video.bit_rate.length, 0)
  assert.equal('download_addr' in detail.video, false)
})

test('official player payload rejects another video ID', () => {
  assert.throws(() => parsePublicPlayerDetail('1234567890123456789', JSON.stringify({
    aweme_detail: { aweme_id: '9876543210987654321', video: {
      play_addr: { url_list: ['https://v26.douyinvod.com/play.mp4'] },
    } },
  })), /不一致/)
})
