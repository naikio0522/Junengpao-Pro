/** Consult the authenticated backend immediately before launching an installer. */
export async function getActiveTaskCount(
  backendPort: number,
  backendApiToken: string,
  request: typeof fetch = fetch,
): Promise<number> {
  if (!Number.isInteger(backendPort) || backendPort < 1 || backendPort > 65535 || !backendApiToken) {
    throw new Error('暂时无法确认任务状态，请稍后重试。')
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 2500)
  try {
    const response = await request(`http://127.0.0.1:${backendPort}/api/tasks/active-count`, {
      signal: controller.signal,
      headers: { 'X-VideoMatrix-Token': backendApiToken },
    })
    if (!response.ok) throw new Error(`任务状态查询返回 ${response.status}`)
    const value = await response.json() as { count?: unknown }
    if (!Number.isSafeInteger(value?.count) || (value.count as number) < 0) {
      throw new Error('任务状态无效')
    }
    return value.count as number
  } catch {
    throw new Error('暂时无法确认任务状态，请稍后重试。')
  } finally {
    clearTimeout(timer)
  }
}

export async function ensureNoActiveTasks(
  backendPort: number,
  backendApiToken: string,
  request: typeof fetch = fetch,
): Promise<void> {
  const count = await getActiveTaskCount(backendPort, backendApiToken, request)
  if (count > 0) throw new Error(`还有 ${count} 项任务正在处理，请等任务完成后再安装更新。`)
}
