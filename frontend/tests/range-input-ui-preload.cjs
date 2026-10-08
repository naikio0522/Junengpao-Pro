localStorage.setItem('vm-workflow', 'mix')
localStorage.setItem('vm-config', JSON.stringify({
  selection_mode: 'random', hook_dir: '', body_dirs: [], base_out_dir: '',
  t_hook: 4, t_body: 6, total_clips: 3,
}))

window.__rangePreflightRequests = []
window.__rangeTaskRequests = []
window.__hookFileFilters = null
window.electronAPI = {
  getBackendPort: async () => 8765,
  openFile: async filters => {
    window.__hookFileFilters = filters
    return 'C:\\clips\\single-hook.mp4'
  },
  openDirectory: async () => 'C:\\clips\\hook-folder',
  openVideoFiles: async () => null,
  openPath: async () => {},
  checkForUpdates: async () => ({ status: 'current', currentVersion: '0.1.2', message: '' }),
  downloadAndInstallUpdate: async () => 'canceled',
  onUpdateDownloadProgress: () => () => {},
}

window.fetch = async (url, options = {}) => {
  const path = String(url)
  if (path.endsWith('/preflight')) {
    window.__rangePreflightRequests.push(JSON.parse(options.body))
    return new Response(JSON.stringify({ ok: true, capacity: 1, report: [] }), {
      headers: { 'Content-Type': 'application/json' },
    })
  }
  if (path.endsWith('/tasks') && options.method === 'POST') {
    window.__rangeTaskRequests.push(JSON.parse(options.body))
    return new Response(JSON.stringify({ task_id: 'range-test', message: 'ok' }), {
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const data = path.endsWith('/health') ? { status: 'ok' }
    : path.endsWith('/tasks') ? []
    : path.endsWith('/scan') ? { files: [], count: 0 }
    : {}
  return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
}
