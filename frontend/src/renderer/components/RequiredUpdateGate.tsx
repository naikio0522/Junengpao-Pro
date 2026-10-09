import { useEffect, useState } from 'react'
import type { UpdateCheckResult, UpdateDownloadProgress } from '../../preload/preload'

type RequiredResult = Extract<UpdateCheckResult, { status: 'available' | 'required' }>

export default function RequiredUpdateGate({ result, onRetry }: {
  result: RequiredResult
  onRetry: () => Promise<void>
}) {
  const [activeCount, setActiveCount] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [progress, setProgress] = useState<UpdateDownloadProgress | null>(null)
  const [message, setMessage] = useState('')

  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const count = await window.electronAPI.getActiveTaskCount()
        if (!disposed) setActiveCount(count)
      } catch {
        if (!disposed) setActiveCount(null)
      }
      if (!disposed) timer = setTimeout(poll, 1000)
    }
    void poll()
    return () => { disposed = true; clearTimeout(timer) }
  }, [])

  useEffect(() => window.electronAPI.onUpdateDownloadProgress(setProgress), [])

  const install = async () => {
    setDownloading(true)
    setProgress(null)
    setMessage('正在确认并准备下载…')
    try {
      const status = await window.electronAPI.downloadAndInstallUpdate()
      setMessage(status === 'canceled' ? '已取消安装，可稍后重试。'
        : status === 'dmg-opened' ? '安装镜像已打开，请按 macOS 提示完成安装，然后重启软件。'
          : '安装向导已启动，软件即将关闭。')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error))
    } finally {
      setDownloading(false)
    }
  }

  return (
    <main className="flex min-h-screen w-screen items-center justify-center bg-background p-5 text-foreground">
      <section role="dialog" aria-label="必须更新巨能跑pro版" aria-modal="true"
        className="w-full max-w-lg rounded-2xl border border-border/25 bg-background-elev p-6 shadow-2xl">
        <p className="text-xs text-accent">巨能跑pro版 · 版本更新</p>
        <h1 className="mt-2 text-xl font-semibold">请更新后继续使用</h1>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          当前版本 v{result.currentVersion} 低于最低支持版本 v{result.update.minimumSupportedVersion}。
          官方安装包版本为 v{result.update.version}。
        </p>
        {result.usingCachedPolicy && <p className="mt-3 text-xs leading-5 text-accent">
          目前无法取得最新更新策略，仍按上次在线确认的要求更新。请联网重试，或手动安装官方最新版。
        </p>}
        {!!result.update.notes && <p className="mt-3 max-h-28 overflow-auto whitespace-pre-wrap text-xs leading-5 text-muted-foreground">{result.update.notes}</p>}
        <p role="status" className="mt-4 text-xs text-muted-foreground">
          {activeCount === null ? '正在确认任务状态，暂不能安装。'
            : activeCount > 0 ? `还有 ${activeCount} 项任务正在处理。任务会继续运行，完成后即可安装。`
              : '当前没有正在处理的任务，可以下载安装。'}
        </p>
        {downloading && progress && <div className="mt-3">
          <div className="flex justify-between text-xs"><span>下载并校验安装包</span><span>{progress.percent}%</span></div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted/40"><div className="h-full bg-accent" style={{ width: `${progress.percent}%` }} /></div>
        </div>}
        {!!message && <p role="status" className="mt-3 break-words text-xs text-accent">{message}</p>}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => void install()} disabled={downloading || activeCount !== 0}
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-background disabled:cursor-not-allowed disabled:opacity-50">
            {downloading ? '更新中…' : '立即更新'}
          </button>
          <button type="button" onClick={() => void onRetry()} disabled={downloading}
            className="rounded-md border border-border/30 px-4 py-2 text-sm disabled:opacity-50">重新检查</button>
          <button type="button" onClick={() => void window.electronAPI.openExternalHttps(result.update.releasePage)}
            className="text-xs text-accent underline">查看官方发布页</button>
        </div>
      </section>
    </main>
  )
}
