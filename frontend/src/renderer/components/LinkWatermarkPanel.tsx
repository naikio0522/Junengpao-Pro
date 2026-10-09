import { useEffect, useRef, useState } from 'react'
import { api, type LinkDownloadJob, type LinkVideoResolution } from '../api/client'

const LAST_JOB_KEY = 'vm-link-download-task-id'
const LAST_OUTPUT_KEY = 'vm-link-download-output-dir'

function safeHttpsUrl(value: string | null): string | null {
  if (!value) return null
  try { return new URL(value).protocol === 'https:' ? value : null }
  catch { return null }
}

interface Props {
  defaultOutputDir?: string
  onLog?: (line: string) => void
}

export function LinkWatermarkPanel({ defaultOutputDir = '', onLog }: Props) {
  const [url, setUrl] = useState('')
  const [authorized, setAuthorized] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [resolved, setResolved] = useState<LinkVideoResolution | null>(null)
  const [formatId, setFormatId] = useState('')
  const [saveCover, setSaveCover] = useState(false)
  const [outputDir, setOutputDir] = useState(() => localStorage.getItem(LAST_OUTPUT_KEY) || defaultOutputDir)
  const [pending, setPending] = useState(false)
  const [job, setJob] = useState<LinkDownloadJob | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const seenLogs = useRef(0)
  const active = pending || job?.status === 'pending' || job?.status === 'running'

  useEffect(() => {
    if (!outputDir && defaultOutputDir) setOutputDir(defaultOutputDir)
  }, [defaultOutputDir, outputDir])

  useEffect(() => {
    const previous = localStorage.getItem(LAST_JOB_KEY)
    if (!previous) return
    let alive = true
    api.getLinkDownloadJob(previous).then(current => {
      if (!alive) return
      seenLogs.current = current.log_lines.length
      setJob(current)
    }).catch(() => localStorage.removeItem(LAST_JOB_KEY))
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!job?.task_id || !['pending', 'running'].includes(job.status)) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const current = await api.getLinkDownloadJob(job.task_id)
        if (!alive) return
        setJob(current)
        for (const line of current.log_lines.slice(seenLogs.current)) onLog?.(line)
        seenLogs.current = current.log_lines.length
        if (current.status === 'pending' || current.status === 'running') timer = setTimeout(poll, 800)
        else localStorage.removeItem(LAST_JOB_KEY)
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause))
      }
    }
    timer = setTimeout(poll, 400)
    return () => { alive = false; if (timer) clearTimeout(timer) }
  }, [job?.task_id, job?.status, onLog])

  const resolve = async () => {
    if (!url.trim() || !authorized || resolving || active) return
    setResolving(true)
    setResolved(null)
    setSaveCover(false)
    setJob(null)
    localStorage.removeItem(LAST_JOB_KEY)
    setError('')
    setNotice('')
    try {
      const result = await api.resolveLinkVideo(url.trim())
      setResolved(result)
      setFormatId(result.formats[0]?.id || '')
      onLog?.(`链接解析完成：${result.title || result.platform}`)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      setError(message)
      onLog?.(`[链接解析失败] ${message}`)
    } finally { setResolving(false) }
  }

  const chooseOutput = async () => {
    const chosen = await window.electronAPI?.openDirectory(outputDir || undefined)
    if (typeof chosen === 'string') {
      setOutputDir(chosen)
      localStorage.setItem(LAST_OUTPUT_KEY, chosen)
    }
  }

  const save = async () => {
    if (!resolved || !authorized || !outputDir.trim() || active) return
    setPending(true)
    setError('')
    setNotice('')
    try {
      const created = await api.startLinkDownload({
        resolve_id: resolved.resolve_id,
        ...(formatId ? { format_id: formatId } : {}),
        output_dir: outputDir.trim(),
        save_cover: saveCover && Boolean(safeHttpsUrl(resolved.thumbnail_url)),
      })
      localStorage.setItem(LAST_JOB_KEY, created.task_id)
      localStorage.setItem(LAST_OUTPUT_KEY, outputDir.trim())
      seenLogs.current = 0
      setJob({ task_id: created.task_id, status: 'pending', progress: 0, stage: '等待下载', output_files: [], errors: [], log_lines: [] })
      onLog?.(`链接视频下载任务已启动：${created.task_id}`)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      setError(message)
      onLog?.(`[链接下载失败] ${message}`)
    } finally { setPending(false) }
  }

  const stop = async () => {
    if (!job?.task_id || !active) return
    try { await api.stopLinkDownloadJob(job.task_id); setNotice('已请求停止当前下载') }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  const copyText = async (value: string, message: string) => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value)
      else {
        const input = document.createElement('textarea')
        input.value = value
        input.style.position = 'fixed'
        input.style.opacity = '0'
        document.body.appendChild(input)
        input.select()
        const copied = document.execCommand('copy')
        input.remove()
        if (!copied) throw new Error('clipboard unavailable')
      }
      setError('')
      setNotice(message)
    } catch { setError('复制失败，请检查系统剪贴板权限') }
  }

  const openCover = async () => {
    const cover = safeHttpsUrl(resolved?.thumbnail_url || null)
    if (!cover || !window.electronAPI?.openExternalHttps) return
    try {
      await window.electronAPI.openExternalHttps(cover)
      setError('')
      setNotice('已在浏览器打开封面，可在浏览器中另存图片')
    } catch { setError('无法打开封面，请复制封面地址后在浏览器中打开') }
  }

  const safeCover = safeHttpsUrl(resolved?.thumbnail_url || null)
  const selectedFormat = resolved?.formats.find(format => format.id === formatId)
  return <section aria-label="短视频链接一键去水印" className="vm-card space-y-4 rounded-[16px] p-4">
    <div>
      <h2 className="text-[15px] font-semibold text-foreground">短视频链接一键去水印</h2>
      <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
        粘贴公开分享链接，解析平台提供的视频源，再保存到本地。当前支持抖音、小红书；快手和私密内容暂不支持。平台源流是否无水印无法保证，请预览后核对。
      </p>
    </div>
    <div className="space-y-3 rounded-[14px] border border-border/[0.16] bg-background-elev/[0.45] p-3">
      <label className="block space-y-1 text-[11px] text-foreground/85">
        <span>短视频分享链接</span>
        <textarea aria-label="短视频分享链接" rows={2} value={url}
          onChange={event => { setUrl(event.target.value); setResolved(null); setError('') }}
          placeholder="粘贴抖音或小红书公开分享链接，也可粘贴包含链接的分享文案"
          className="w-full resize-y rounded-[9px] border border-border/[0.16] bg-background-elev px-3 py-2 text-[11px] text-foreground outline-none placeholder:text-muted-foreground/55 focus:border-accent/70" />
      </label>
      <label className="inline-flex cursor-pointer items-start gap-2 text-[11px] leading-5 text-muted-foreground">
        <input type="checkbox" checked={authorized} onChange={event => setAuthorized(event.target.checked)}
          className="mt-1 accent-[rgb(var(--color-accent))]" />
        <span>我拥有该视频的版权或已取得下载、处理授权；不会用于侵犯他人权益。</span>
      </label>
      <button type="button" disabled={!url.trim() || !authorized || resolving || active} onClick={() => void resolve()}
        className="vm-action-primary h-9 px-4 text-[11px] disabled:cursor-not-allowed disabled:opacity-45">
        {resolving ? '正在解析…' : '解析链接'}
      </button>
    </div>
    {error && <p role="alert" className="rounded-[9px] border border-hot/30 bg-hot/[0.05] px-3 py-2 text-[11px] text-hot">{error}</p>}
    {notice && <p role="status" className="text-[11px] text-accent">{notice}</p>}
    {resolved && <div aria-label="链接解析结果" className="space-y-3 rounded-[14px] border border-accent/20 bg-accent/[0.035] p-3">
      <div className="flex gap-3">
        {safeCover && <img src={safeCover} alt="视频封面预览" referrerPolicy="no-referrer" className="h-24 w-16 shrink-0 rounded-[8px] bg-background object-cover" />}
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-[12px] font-semibold text-foreground">{resolved.title || '未命名视频'}</p>
          <p className="mt-1 text-[10px] text-muted-foreground">来源：{resolved.platform} · 视频源：{(selectedFormat?.watermark_status || resolved.watermark_status) === 'original' ? '平台原始流' : '水印状态未核验'}</p>
          <p className="mt-1 text-[10px] leading-4 text-amber-400">{resolved.warning || '平台提供的视频源可能带水印，保存前请核对。'} 即使是原始流，仍请核对成片画面。</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" onClick={() => void copyText(resolved.title || '未命名视频', '视频标题已复制')} className="text-[10px] text-accent hover:underline">复制标题</button>
            {safeCover && <button type="button" onClick={() => void copyText(safeCover, '封面地址已复制')} className="text-[10px] text-accent hover:underline">复制封面地址</button>}
            {safeCover && <button type="button" onClick={() => void openCover()} className="text-[10px] text-accent hover:underline">打开封面（可另存）</button>}
            {resolved.video_url && <button type="button" onClick={() => void copyText(resolved.video_url!, '视频源地址已复制；临时地址可能失效')} className="text-[10px] text-accent hover:underline">复制视频源地址</button>}
            {safeHttpsUrl(resolved.webpage_url) && <button type="button" onClick={() => void window.electronAPI?.openExternalHttps(resolved.webpage_url)} className="text-[10px] text-accent hover:underline">打开原视频</button>}
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]">
        <label className="space-y-1 text-[10px] text-muted-foreground">
          <span>视频画质</span>
          <select aria-label="下载画质" value={formatId} onChange={event => setFormatId(event.target.value)}
            className="h-9 w-full rounded-[9px] border border-border/[0.16] bg-background-elev px-2 text-[11px] text-foreground">
            {resolved.formats.length ? resolved.formats.map(format => <option key={format.id} value={format.id}>{format.label}</option>)
              : <option value="">自动选择</option>}
          </select>
        </label>
        <label className="space-y-1 text-[10px] text-muted-foreground">
          <span>保存文件夹</span>
          <div className="flex gap-1.5">
            <input aria-label="链接视频保存文件夹" value={outputDir} onChange={event => setOutputDir(event.target.value)}
              placeholder="选择输出目录" className="h-9 min-w-0 flex-1 rounded-[9px] border border-border/[0.16] bg-background-elev px-3 font-mono text-[11px] text-foreground outline-none focus:border-accent/70" />
            <button type="button" onClick={() => void chooseOutput()} className="vm-action-secondary h-9 px-3 text-[11px]">选择</button>
          </div>
        </label>
        <button type="button" disabled={!outputDir.trim() || !authorized || active} onClick={() => void save()}
          className="vm-action-primary h-9 self-end px-4 text-[11px] disabled:cursor-not-allowed disabled:opacity-45">
          {active ? '保存中…' : saveCover && safeCover ? '保存视频和封面' : '保存视频'}
        </button>
      </div>
      {safeCover && <label className="inline-flex cursor-pointer items-center gap-2 text-[11px] text-muted-foreground">
        <input type="checkbox" checked={saveCover} onChange={event => setSaveCover(event.target.checked)}
          className="accent-[rgb(var(--color-accent))]" />
        <span>同时保存封面到输出文件夹</span>
      </label>}
    </div>}
    {job && <div aria-label="链接视频下载进度" className="space-y-2 rounded-[14px] border border-border/[0.16] bg-background-elev/[0.35] p-3">
      <div className="flex items-center justify-between gap-2 text-[11px]"><span>{job.stage || '下载任务'}</span><span className="font-mono text-accent">{Math.max(0, Math.min(100, Math.round(job.progress)))}%</span></div>
      <div className="h-1.5 overflow-hidden rounded-full bg-foreground/[0.08]"><div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} /></div>
      {active && <button type="button" onClick={() => void stop()} className="text-[10px] text-hot hover:underline">停止下载</button>}
      {job.errors.map((message, index) => <p key={index} className="text-[10px] text-hot">{message}</p>)}
      {!!job.log_lines.length && <div aria-label="链接视频处理日志" className="max-h-28 space-y-1 overflow-y-auto rounded-[8px] bg-background/35 p-2 font-mono text-[10px] text-muted-foreground">
        {job.log_lines.slice(-12).map((line, index) => <div key={`${index}-${line}`}>{line}</div>)}
      </div>}
      {job.output_files.map(file => <div key={file} className="flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-ok/20 bg-ok/[0.04] px-2.5 py-1.5 text-[10px]">
        <span className="min-w-0 break-all font-mono text-foreground">{file}</span>
        <button type="button" onClick={() => void window.electronAPI?.openPath(file)} className="shrink-0 text-accent hover:underline">{file.toLowerCase().endsWith('.mp4') ? '打开视频' : '打开封面'}</button>
      </div>)}
      {job.status === 'completed' && !!job.output_files.length && <button type="button" onClick={() => void window.electronAPI?.openPath(outputDir)} className="text-[10px] text-accent hover:underline">打开保存文件夹</button>}
    </div>}
  </section>
}
