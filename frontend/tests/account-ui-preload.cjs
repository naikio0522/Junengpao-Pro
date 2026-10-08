localStorage.setItem('vm-config', JSON.stringify({
  selection_mode: 'random', hook_dir: 'C:/clips/hook', body_dirs: ['C:/clips/body'],
  base_out_dir: 'C:/clips/output',
}))

window.electronAPI = {
  getBackendPort: async () => 8765,
  onUpdateDownloadProgress: () => () => {},
}

window.__accountCalls = []
const phone = '13800138000'
const password = 'local-test-pass-42'
const token = 'local-test-token-0123456789-abcdefghijklmnopqrstuvwxyz'
const user = { id: 'account-test-id', phone, created_at: '2026-10-08T09:00:00+00:00', phone_verified: false }

function reply(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

window.fetch = async (url, options = {}) => {
  const path = String(url)
  if (path.endsWith('/api/account/register') || path.endsWith('/api/account/login')) {
    const body = JSON.parse(options.body)
    window.__accountCalls.push({
      path, method: options.method, phone: body.phone,
      hasPassword: Object.hasOwn(body, 'password'),
      passwordMatchesExpected: body.password === password,
      // Deliberately do not retain a plaintext password in this test spy.
    })
    if (body.phone !== phone || body.password !== password) return reply({ detail: '手机号或密码错误' }, 401)
    return reply({ token, user }, path.endsWith('/register') ? 201 : 200)
  }
  if (path.endsWith('/api/account/me')) {
    window.__accountCalls.push({ path, bearer: options.headers?.Authorization })
    return options.headers?.Authorization === `Bearer ${token}`
      ? reply({ user }) : reply({ detail: '请先登录' }, 401)
  }
  if (path.endsWith('/api/account/logout')) {
    window.__accountCalls.push({ path, method: options.method, bearer: options.headers?.Authorization })
    return options.headers?.Authorization === `Bearer ${token}`
      ? reply({ message: '已退出登录' }) : reply({ detail: '请先登录' }, 401)
  }
  if (path.endsWith('/health')) return reply({ status: 'ok' })
  if (path.endsWith('/tasks')) return reply([])
  if (path.endsWith('/scan')) return reply({ files: [], count: 0 })
  return reply({})
}
