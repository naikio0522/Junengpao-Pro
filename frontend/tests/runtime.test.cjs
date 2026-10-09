const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')
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

test('BGM removal restores clip mode while preserving manual and audio settings', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  useStore.getState().setConfig({ bgm_dir: 'music', duration_mode: 'bgm', total_clips: 7, vol_bgm: 45 })
  assert.equal(useStore.getState().config.duration_mode, 'bgm')
  useStore.getState().setConfig({ bgm_dir: '  ' })
  assert.equal(useStore.getState().config.duration_mode, 'clips')
  assert.equal(useStore.getState().config.total_clips, 7)
  assert.equal(useStore.getState().config.vol_bgm, 45)
  useStore.getState().setConfig({ bgm_dir: 'new music' })
  assert.equal(useStore.getState().config.duration_mode, 'clips')
})

test('output defaults are 1080p 8000k 30fps GPU enabled, saved choices remain intact', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  let config = load('src/renderer/store.ts').useStore.getState().config
  assert.equal(config.resolution, '1080*1920')
  assert.equal(config.bitrate, '8000k')
  assert.equal(config.fps, '30')
  assert.equal(config.enable_gpu, true)
  global.localStorage = { getItem: () => JSON.stringify({ bitrate: '12000k', fps: '24', enable_gpu: false }), setItem: () => {} }
  config = load('src/renderer/store.ts').useStore.getState().config
  assert.equal(config.bitrate, '12000k')
  assert.equal(config.fps, '24')
  assert.equal(config.enable_gpu, false)
})

test('old fixed seconds migrate to equal ranges and new ranges reach the backend request', () => {
  global.localStorage = {
    getItem: () => JSON.stringify({ t_hook: 4, t_body: 6 }), setItem: () => {},
  }
  const { useStore } = load('src/renderer/store.ts')
  const { normalizeConfigForRequest } = load('src/renderer/api/client.ts')
  const migrated = useStore.getState().config
  assert.deepEqual([migrated.t_hook_min, migrated.t_hook_max], [4, 4])
  assert.deepEqual([migrated.t_body_min, migrated.t_body_max], [6, 6])
  const request = normalizeConfigForRequest({ ...migrated, t_hook_min: '2', t_hook_max: '5', t_body_min: '3', t_body_max: '8' })
  assert.deepEqual([request.t_hook_min, request.t_hook_max, request.t_body_min, request.t_body_max], [2, 5, 3, 8])
  assert.equal(request.t_hook, 2)
  assert.equal(request.t_body, 3)
})

test('full Body flags survive request normalization and ignore disabled duration values', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const { normalizeConfigForRequest } = load('src/renderer/api/client.ts')
  const base = useStore.getState().config
  const normal = normalizeConfigForRequest({ ...base, body_full_duration: true, t_body: '', body_r: '' })
  assert.equal(normal.body_full_duration, true)
  assert.equal(normal.t_body, 3)
  assert.equal(normal.body_r, 0)
  const grouped = normalizeConfigForRequest({ ...base, body_mode: 'grouped', body_groups: [
    { enabled: true, folder: 'a', clip_count: 2, clip_duration: '', full_duration: true },
    { enabled: true, folder: 'b', clip_count: 1, clip_duration: 4, full_duration: false },
  ] })
  assert.deepEqual(grouped.body_groups.map(g => g.full_duration), [true, false])
  assert.equal(grouped.body_groups[1].clip_duration, 4)
})

test('speech logic requires separate source folders, 2–6 spoken units and both original voices', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const { speechLogicConfigError } = load('src/renderer/speechLogic.ts')
  const base = { ...useStore.getState().config, selection_mode: 'speech_logic', hook_dir: 'C:\\clips\\hook' }
  assert.match(speechLogicConfigError(base), /分别选择 Hook 和 Body/)
  assert.match(speechLogicConfigError({ ...base, body_dirs: ['c:/clips/hook/'] }), /不能相同/)
  const valid = { ...base, body_dirs: ['C:\\clips\\body'], total_clips: 5 }
  assert.equal(speechLogicConfigError(valid), null)
  assert.equal(speechLogicConfigError({ ...valid, total_clips: 2 }), null)
  assert.match(speechLogicConfigError({ ...valid, total_clips: 1 }), /2–6/)
  assert.match(speechLogicConfigError({ ...valid, total_clips: '4.5' }), /2–6/)
  assert.match(speechLogicConfigError({ ...valid, vol_hook_orig: 0 }), /原声/)
  assert.match(speechLogicConfigError({ ...valid, vol_orig: 0 }), /原声/)
  assert.match(speechLogicConfigError({ ...valid, enable_srt: true, srt_dir: '' }), /字幕/)
})

test('speech logic requests ignore stale random-cut and topic controls while retaining product filter', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const { normalizeConfigForRequest } = load('src/renderer/api/client.ts')
  const base = useStore.getState().config
  const normalized = normalizeConfigForRequest({ ...base, selection_mode: 'speech_logic',
    semantic_sku: ' 99 ', semantic_topic: ' 牙齿敏感 ', t_hook: '', t_body: '',
    hook_r: '', body_r: '', bgm_r: '', hook_full_duration: true, body_full_duration: true })
  assert.equal(normalized.semantic_sku, '99')
  assert.equal(normalized.semantic_topic, '')
  assert.equal(normalized.t_hook, 3)
  assert.equal(normalized.t_body, 3)
  assert.equal(normalized.hook_r, 0.5)
  assert.equal(normalized.body_r, 0.5)
  assert.equal(normalized.hook_full_duration, false)
  assert.equal(normalized.body_full_duration, false)
  useStore.getState().setConfig({ selection_mode: 'speech_logic', body_mode: 'grouped', bgm_dir: 'music', duration_mode: 'bgm' })
  assert.equal(useStore.getState().config.body_mode, 'normal')
  assert.equal(useStore.getState().config.duration_mode, 'clips')
})

test('startup rejects old versions, unrelated instances and invalid ports', () => {
  const { validateIdentity } = load('src/main/backendHandshake.ts')
  assert.doesNotThrow(() => validateIdentity({ port: 53001, version: '2.3.2', instance: 'owned' }, '2.3.2', 'owned'))
  for (const response of [
    { status: 'ok' },
    { port: 53001, version: '2.3.1', instance: 'owned' },
    { port: 53001, version: '2.3.2', instance: 'another-process' },
    { port: 0, version: '2.3.2', instance: 'owned' },
  ]) assert.throws(() => validateIdentity(response, '2.3.2', 'owned'))
})

test('packaged account mode stays cloud even with inherited dev and local-test environment', () => {
  const { resolveAccountBackendConfig } = load('src/main/accountBackendConfig.ts')
  const packaged = resolveAccountBackendConfig(true, {
    NODE_ENV: 'development', JNP_ACCOUNT_MODE: 'local_test',
    JNP_ACCOUNT_API_URL: 'https://runtime.example.com',
  }, 'https://release.example.com')
  assert.deepEqual(packaged, { mode: 'cloud', apiUrl: 'https://release.example.com' })
  assert.deepEqual(resolveAccountBackendConfig(true, { JNP_ACCOUNT_MODE: 'local_test' }, ''),
    { mode: 'cloud', apiUrl: '' })
  assert.deepEqual(resolveAccountBackendConfig(false, {}, ''),
    { mode: 'local_test', apiUrl: '' })
  assert.deepEqual(resolveAccountBackendConfig(false, { JNP_ACCOUNT_API_URL: 'https://test.example.com' }, ''),
    { mode: 'cloud', apiUrl: 'https://test.example.com' })
})

test('real store shows backend logs once, retains cursor after clear and captures final logs', () => {
  global.localStorage = { getItem: () => null, setItem: () => {} }
  const { useStore } = load('src/renderer/store.ts')
  const task = { task_id: 'a', status: 'running', log_lines: ['[加速] NVIDIA'] }
  useStore.getState().setTasks([task])
  useStore.getState().setTasks([task])
  assert.deepEqual(useStore.getState().logs, ['[加速] NVIDIA'])
  useStore.getState().clearLogs()
  useStore.getState().setTasks([task])
  assert.deepEqual(useStore.getState().logs, [])
  useStore.getState().setTasks([{ ...task, status: 'completed', log_lines: [...task.log_lines, '[加速降级] CPU'] }])
  assert.deepEqual(useStore.getState().logs, ['[加速降级] CPU'])
})
