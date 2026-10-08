// Deterministic fixture: the segmented workflow test must not call a real backend.
localStorage.setItem('vm-workflow', 'mix')
localStorage.setItem('vm-config', JSON.stringify({
  selection_mode: 'speech_logic',
  hook_dir: 'C:\\clips\\hook',
  body_dirs: ['C:\\clips\\body'],
  base_out_dir: 'C:\\clips\\output',
}))

window.electronAPI = {
  getBackendPort: async () => 8765,
  onUpdateDownloadProgress: () => () => {},
}
window.fetch = async url => new Response(JSON.stringify(
  String(url).endsWith('/health') ? { status: 'ok' }
    : String(url).endsWith('/tasks') ? [] : { files: [], count: 0 },
), { headers: { 'Content-Type': 'application/json' } })
