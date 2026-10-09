window.__policyMode = 'required'
window.__activeCount = 1
window.__installCalls = 0

window.electronAPI = {
  isPackaged: async () => true,
  getBackendPort: async () => 8765,
  getBackendToken: async () => 'test-token',
  checkForUpdates: async () => {
    if (window.__policyMode === 'offline') throw new Error('fetch failed')
    return { status: 'required', currentVersion: '0.1.8', update: {
      version: '0.1.9', minimumSupportedVersion: '0.1.9', notes: '更新说明',
      releasePage: 'https://github.com/naikio0522/Junengpao-Pro/releases/tag/v0.1.9', size: 42,
    }, usingCachedPolicy: window.__policyMode === 'cached' }
  },
  checkForStartupUpdate: async () => window.electronAPI.checkForUpdates(),
  openExternalHttps: async () => {},
  getActiveTaskCount: async () => window.__activeCount,
  downloadAndInstallUpdate: async () => { window.__installCalls++; return 'canceled' },
  onUpdateDownloadProgress: () => () => {},
}

window.fetch = async url => new Response(JSON.stringify(
  String(url).endsWith('/health') ? { status: 'ok' } : []
), { headers: { 'Content-Type': 'application/json' } })
