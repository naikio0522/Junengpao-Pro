import { useEffect, useRef, useState } from 'react'
import type { UpdateCheckResult, UpdateDownloadProgress } from '../../preload/preload'

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function CheckUpdatesButton() {
  const [open, setOpen] = useState(false)
  const [checking, setChecking] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [result, setResult] = useState<UpdateCheckResult | null>(null)
  const [message, setMessage] = useState('')
  const [progress, setProgress] = useState<UpdateDownloadProgress | null>(null)
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  useEffect(() => window.electronAPI?.onUpdateDownloadProgress?.(setProgress), [])

  const check = async () => {
    setOpen(true)
    setChecking(true)
    setMessage('')
    setResult(null)
    try {
      const checked = await window.electronAPI.checkForUpdates()
      setResult(checked)
    } catch (error) {
      setMessage(`检查失败：${readableError(error)}`)
    } finally {
      setChecking(false)
    }
  }

  const install = async () => {
    setDownloading(true)
    setProgress(null)
    setMessage('正在确认并准备下载…')
    try {
      const status = await window.electronAPI.downloadAndInstallUpdate()
      setMessage(status === 'canceled' ? '已取消更新。'
        : status === 'dmg-opened' ? '安装镜像已打开，请按 macOS 提示完成安装。'
          : '安装向导已启动，软件即将关闭。')
    } catch (error) {
      setMessage(`更新失败：${readableError(error)}`)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div ref={container} className="relative z-50 shrink-0">
      <button type="button" onClick={() => open ? setOpen(false) : void check()}
        aria-label="检查更新" aria-expanded={open}
        className="h-7 rounded-[4px] border border-border/[0.14] bg-foreground/[0.02] px-2.5 text-[11px] text-foreground hover:border-accent/60 hover:text-accent">
        检查更新
      </button>
      {open && <div role="dialog" aria-label="版本更新" className="absolute right-0 top-full mt-2 w-[min(320px,calc(100vw-32px))] rounded-[14px] border border-border/20 bg-background-elev p-3 text-[11px] leading-5 text-foreground shadow-2xl">
        <div className="flex items-center justify-between gap-2">
          <span className="font-semibold">巨能跑pro版更新</span>
          <button type="button" onClick={() => setOpen(false)} aria-label="关闭更新面板" className="text-muted-foreground hover:text-foreground">关闭</button>
        </div>
        {checking && <p className="mt-2 text-accent">正在检查是否为最新版本...</p>}
        {(result?.status === 'available' || result?.status === 'required') && <div className="mt-2 space-y-2">
          <p>{result.status === 'required' ? '当前版本需要更新' : '发现新版本'}：v{result.update.version}（当前 v{result.currentVersion}）</p>
          {!!result.update.notes && <p className="max-h-24 overflow-auto whitespace-pre-wrap text-muted-foreground">{result.update.notes}</p>}
          <button type="button" onClick={() => void install()} disabled={downloading}
            className="rounded-[4px] bg-accent px-3 py-1.5 font-semibold text-background disabled:opacity-50">
            {downloading ? '更新中…' : '立即更新'}
          </button>
        </div>}
        {result && (result.status === 'current' || result.status === 'unpublished') &&
          <p className="mt-2 text-muted-foreground">您已经是最新版本啦</p>}
        {result?.status === 'unsupported' &&
          <p className="mt-2 text-muted-foreground">{result.message}</p>}
        {downloading && progress && <div className="mt-2">
          <div className="flex justify-between"><span>下载并校验安装包</span><span>{progress.percent}%</span></div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted/40"><div className="h-full bg-accent" style={{ width: `${progress.percent}%` }} /></div>
        </div>}
        {!!message && <p className="mt-2 break-words text-accent">{message}</p>}
      </div>}
    </div>
  )
}
