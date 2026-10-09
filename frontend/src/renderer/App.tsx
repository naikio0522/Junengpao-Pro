import { useCallback, useEffect, useState } from 'react'
import { useStore } from './store'
import { api } from './api/client'
import SinglePage from './components/SinglePage'
import Toast from './components/animation/Toast'
import RequiredUpdateGate from './components/RequiredUpdateGate'
import type { UpdateCheckResult } from '../preload/preload'

function App() {
  const { backendReady, setBackendReady, setTasks } = useStore()
  const [checkingUpdate, setCheckingUpdate] = useState(true)
  const [requiredUpdate, setRequiredUpdate] = useState<Extract<UpdateCheckResult,
    { status: 'available' | 'required' }> | null>(null)
  const [updateCheckFailed, setUpdateCheckFailed] = useState(false)

  const checkStartupUpdate = useCallback(async () => {
    setCheckingUpdate(true)
    try {
      if (await window.electronAPI?.isPackaged?.() === false) {
        setRequiredUpdate(null)
        setUpdateCheckFailed(false)
        return
      }
      const result = await (window.electronAPI?.checkForStartupUpdate?.()
        || window.electronAPI?.checkForUpdates?.())
      setRequiredUpdate(result?.status === 'required' ? result : null)
      setUpdateCheckFailed(false)
    } catch {
      // A transient network or malformed remote response must never make the
      // installed app unusable. The next launch will check the live policy again.
      setRequiredUpdate(null)
      setUpdateCheckFailed(true)
    } finally {
      setCheckingUpdate(false)
    }
  }, [])

  useEffect(() => { void checkStartupUpdate() }, [checkStartupUpdate])

  useEffect(() => {
    const check = async () => {
      try { await api.health(); setBackendReady(true) }
      catch { setTimeout(check, 1000) }
    }
    check()
  }, [setBackendReady])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const tasks = await api.listTasks()
        if (!cancelled) setTasks(tasks)
      } catch { /* Retry transient connection failures without losing log cursors. */ }
      if (!cancelled) timer = setTimeout(poll, 1000)
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [setTasks])

  if (!backendReady || checkingUpdate) {
    return (
      <div className="flex items-center justify-center w-screen h-screen bg-background">
        <div className="flex items-center gap-2.5 text-xs font-mono text-muted-foreground">
          <span className="w-2 h-2 bg-accent animate-pulse-dot" />
          {checkingUpdate ? '正在检查更新策略' : '正在启动后端服务'}
        </div>
      </div>
    )
  }

  if (requiredUpdate) {
    return <RequiredUpdateGate result={requiredUpdate} onRetry={checkStartupUpdate} />
  }

  return (
    <div className="flex flex-col w-screen h-screen bg-background text-foreground overflow-hidden">
      <SinglePage />
      <Toast />
      {updateCheckFailed && <div role="status" className="fixed bottom-4 left-1/2 z-[1001] -translate-x-1/2 rounded-lg border border-border/30 bg-background-elev px-4 py-2 text-xs text-foreground shadow-xl">
        自动检查更新失败，软件仍可使用。请稍后在标题栏重试。
        <button type="button" className="ml-3 text-accent" onClick={() => setUpdateCheckFailed(false)}>关闭</button>
      </div>}
    </div>
  )
}

export default App
