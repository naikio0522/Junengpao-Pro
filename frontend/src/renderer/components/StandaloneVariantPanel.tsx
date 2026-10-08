import { useEffect, useRef, useState, type DragEvent } from 'react'

type Strength = 'mild' | 'balanced' | 'strong'
type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'stopped'

interface VariantJob {
  task_id: string
  status: JobStatus
  progress: number
  current: number
  total: number
  file_progress?: number
  phase_detail?: string
  input_count: number
  output_dir: string
  output_files: string[]
  errors: string[]
  log_lines: string[]
  message: string
}

interface StandaloneVariantPanelProps {
  onTaskStarted?: (taskId: string) => void
  onLog?: (message: string) => void
}

const strengths: { value: Strength; label: string; hint: string }[] = [
  { value: 'mild', label: '轻度', hint: '画面变化较小' },
  { value: 'balanced', label: '标准', hint: '适合大多数视频' },
  { value: 'strong', label: '增强', hint: '画面变化较明显' },
]
const LAST_JOB_KEY = 'vm-standalone-variant-task-id'

function fileName(path: string): string {
  return path.split(/[\\/]/).at(-1) || path
}

async function variantRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const port = await window.electronAPI.getBackendPort()
  const response = await fetch(`http://127.0.0.1:${port}/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`
    try {
      const data = await response.json()
      if (typeof data.detail === 'string') detail = data.detail
    } catch { /* keep the HTTP error */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

export function StandaloneVariantPanel({ onTaskStarted, onLog }: StandaloneVariantPanelProps) {
  const [paths, setPaths] = useState<string[]>([])
  const [outputDir, setOutputDir] = useState('')
  const [strength, setStrength] = useState<Strength>('balanced')
  const [copies, setCopies] = useState(1)
  const [allowMirror, setAllowMirror] = useState(false)
  const [frameMix, setFrameMix] = useState(true)
  const [useGpu, setUseGpu] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [job, setJob] = useState<VariantJob | null>(null)
  const [restoring, setRestoring] = useState(() => Boolean(localStorage.getItem(LAST_JOB_KEY)))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const seenLogs = useRef(0)
  const active = restoring || pending || job?.status === 'pending' || job?.status === 'running'

  useEffect(() => {
    const taskId = localStorage.getItem(LAST_JOB_KEY)
    if (!taskId) return
    let alive = true
    variantRequest<VariantJob>(`/standalone-variants/${taskId}`)
      .then(current => {
        if (!alive) return
        seenLogs.current = current.log_lines.length
        setJob(current)
      })
      .catch(() => localStorage.removeItem(LAST_JOB_KEY))
      .finally(() => { if (alive) setRestoring(false) })
    return () => { alive = false }
  }, [])

  function addPaths(values: string[]) {
    setPaths(current => [...new Set([...current, ...values.map(value => value.trim()).filter(Boolean)])])
    setError('')
  }

  async function chooseVideos() {
    const selected = await window.electronAPI.openVideoFiles()
    if (selected) addPaths(selected)
  }

  async function chooseFolder() {
    const selected = await window.electronAPI.openDirectory()
    if (typeof selected === 'string') addPaths([selected])
  }

  async function chooseOutput() {
    const selected = await window.electronAPI.openDirectory(outputDir || undefined)
    if (typeof selected === 'string') setOutputDir(selected)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (active) return
    // Electron 28 exposes the absolute path on files dropped from Explorer/Finder.
    const dropped = Array.from(event.dataTransfer.files)
      .map(file => (file as File & { path?: string }).path || '')
      .filter(Boolean)
    if (dropped.length) addPaths(dropped)
    else setError('无法读取拖入的路径，请用“选择视频”或“选择文件夹”添加素材')
  }

  async function start() {
    if (paths.length === 0) {
      setError('先添加视频或文件夹')
      return
    }
    setPending(true)
    setError('')
    try {
      const created = await variantRequest<VariantJob>('/standalone-variants', {
        method: 'POST',
        body: JSON.stringify({
          input_paths: paths,
          output_dir: outputDir,
          strength,
          copies_per_video: copies,
          allow_mirror: allowMirror,
          frame_mix: frameMix,
          use_gpu: useGpu,
        }),
      })
      seenLogs.current = 0
      localStorage.setItem(LAST_JOB_KEY, created.task_id)
      setJob(created)
      onTaskStarted?.(created.task_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(false)
    }
  }

  async function stop() {
    if (!job) return
    try {
      await variantRequest(`/standalone-variants/${job.task_id}/stop`, { method: 'POST' })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  useEffect(() => {
    if (!job?.task_id || !['pending', 'running'].includes(job.status)) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const current = await variantRequest<VariantJob>(`/standalone-variants/${job.task_id}`)
        if (!alive) return
        setJob(current)
        for (const line of current.log_lines.slice(seenLogs.current)) onLog?.(line)
        seenLogs.current = current.log_lines.length
        if (current.status === 'pending' || current.status === 'running') {
          timer = setTimeout(poll, 900)
        }
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause))
      }
    }
    timer = setTimeout(poll, 500)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [job?.task_id, job?.status, onLog])

  return (
    <section className="space-y-4 rounded-md border border-border/20 bg-background-elev/50 p-4" aria-label="已有视频去重变换">
      <div>
        <h2 className="text-sm font-semibold text-foreground">已有视频去重变换</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">直接处理已有视频，保留原文件，完成后在新文件夹中查看结果。</p>
      </div>

      <div
        aria-label="拖入视频或文件夹"
        onDragEnter={event => { event.preventDefault(); if (!active) setDragging(true) }}
        onDragOver={event => event.preventDefault()}
        onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false) }}
        onDrop={handleDrop}
        className={`rounded-md border border-dashed p-5 text-center transition-colors ${dragging ? 'border-accent bg-accent/10' : 'border-border/40 bg-background/50'}`}
      >
        <div className="text-sm font-medium text-foreground">拖入视频或文件夹</div>
        <p className="mt-1 text-xs text-muted-foreground">也可以点下方按钮添加，支持批量视频和文件夹</p>
        <div className="mt-3 flex justify-center gap-2">
          <button type="button" disabled={active} onClick={chooseVideos} className="rounded border border-border/40 px-3 py-1.5 text-xs text-foreground hover:border-accent disabled:opacity-50">选择视频</button>
          <button type="button" disabled={active} onClick={chooseFolder} className="rounded border border-border/40 px-3 py-1.5 text-xs text-foreground hover:border-accent disabled:opacity-50">选择文件夹</button>
        </div>
      </div>

      {paths.length > 0 && (
        <div>
          <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
            <span>已添加 {paths.length} 个入口；文件夹内视频会自动查找</span>
            <button type="button" disabled={active} onClick={() => setPaths([])} className="text-accent hover:underline disabled:opacity-50">清空</button>
          </div>
          <div className="max-h-28 space-y-1 overflow-auto">
            {paths.map(path => (
              <div key={path} className="flex items-center justify-between gap-2 rounded bg-background/60 px-2 py-1 text-xs">
                <span title={path} className="min-w-0 truncate text-foreground">{fileName(path)}</span>
                <button type="button" disabled={active} aria-label={`移除 ${fileName(path)}`} onClick={() => setPaths(current => current.filter(item => item !== path))} className="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50">移除</button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-3">
        <label className="block text-xs text-foreground">
          输出到哪里
          <div className="mt-1 flex gap-2">
            <input value={outputDir} disabled={active} onChange={event => setOutputDir(event.target.value)} placeholder="默认：素材旁的“去重变换输出”文件夹" className="min-w-0 flex-1 rounded border border-border/30 bg-background px-2 py-1.5 text-xs outline-none focus:border-accent disabled:opacity-50" />
            <button type="button" disabled={active} onClick={chooseOutput} className="rounded border border-border/40 px-3 py-1.5 text-xs hover:border-accent disabled:opacity-50">浏览</button>
          </div>
        </label>

        <fieldset className="space-y-2">
          <legend className="text-xs text-foreground">变化程度</legend>
          <div className="grid grid-cols-3 gap-2">
            {strengths.map(item => (
              <button key={item.value} type="button" disabled={active} onClick={() => setStrength(item.value)} aria-pressed={strength === item.value} className={`rounded border p-2 text-left disabled:opacity-50 ${strength === item.value ? 'border-accent bg-accent/10 text-accent' : 'border-border/30 text-foreground hover:border-accent/60'}`}>
                <span className="block text-xs font-medium">{item.label}</span>
                <span className="mt-0.5 block text-[10px] text-muted-foreground">{item.hint}</span>
              </button>
            ))}
          </div>
        </fieldset>

        <label className="flex items-center gap-3 text-xs text-foreground">
          每个视频生成
          <input type="number" min={1} max={10} step={1} value={copies} disabled={active} onChange={event => setCopies(Math.max(1, Math.min(10, Number(event.target.value) || 1)))} className="w-16 rounded border border-border/30 bg-background px-2 py-1.5 text-center outline-none focus:border-accent disabled:opacity-50" />
          份不同版本
        </label>

        <details className="rounded border border-border/20 px-3 py-2 text-xs">
          <summary className="cursor-pointer text-foreground">更多设置</summary>
          <div className="mt-3 space-y-2 text-muted-foreground">
            <label className="flex items-center gap-2"><input type="checkbox" disabled={active} checked={allowMirror} onChange={event => setAllowMirror(event.target.checked)} />允许随机镜像画面</label>
            <label className="flex items-center gap-2"><input type="checkbox" disabled={active} checked={frameMix} onChange={event => setFrameMix(event.target.checked)} />轻微混合相邻画面帧</label>
            <label className="flex items-center gap-2"><input type="checkbox" disabled={active} checked={useGpu} onChange={event => setUseGpu(event.target.checked)} />尝试显卡编码加速</label>
          </div>
        </details>
      </div>

      {error && <p role="alert" className="rounded bg-red-500/10 p-2 text-xs text-red-500">{error}</p>}
      <div className="flex gap-2">
        <button type="button" disabled={active || paths.length === 0} onClick={start} className="rounded bg-accent px-4 py-2 text-xs font-semibold text-background hover:opacity-90 disabled:opacity-50">开始去重变换</button>
        {active && job && <button type="button" onClick={stop} className="rounded border border-border/40 px-4 py-2 text-xs text-foreground hover:border-accent">停止</button>}
      </div>
      <p className="text-[11px] leading-4 text-muted-foreground">此功能会调整画面和声音细节；平台对重复内容的判定由平台决定。</p>

      {job && (
        <div className="space-y-2 rounded-md border border-border/20 bg-background/50 p-3">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="text-foreground">{job.message}</span>
            <span className="shrink-0 text-muted-foreground">{job.current}/{job.total}</span>
          </div>
          <div role="progressbar" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-border/30"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} /></div>
          <div className="rounded border border-border/20 bg-background/60 p-2 text-xs">
            <div className="flex items-center justify-between gap-2 text-muted-foreground">
              <span>当前处理</span>
              <span>本条 {job.file_progress ?? 0}%</span>
            </div>
            <p role="status" aria-live="polite" className="mt-1 break-words leading-5 text-foreground">{job.phase_detail || job.message}</p>
          </div>
          {job.log_lines.length > 0 && (
            <div className="rounded border border-border/20 bg-background/60 p-2 text-xs">
              <div className="mb-1 text-foreground">处理过程{job.log_lines.length > 100 ? '（最近 100 条）' : ''}</div>
              <ol className="max-h-48 space-y-1 overflow-y-auto pr-1 text-[11px] leading-5 text-muted-foreground">
                {job.log_lines.slice(-100).map((item, index) => <li key={`${job.log_lines.length - 100 + index}-${item}`} className="break-words">{item}</li>)}
              </ol>
            </div>
          )}
          {job.output_files.length > 0 && <p className="text-xs text-foreground">已生成 {job.output_files.length} 个视频</p>}
          {job.output_dir && <button type="button" onClick={() => window.electronAPI.openPath(job.output_dir)} className="text-xs text-accent hover:underline">打开输出文件夹</button>}
          {job.errors.length > 0 && <div className="max-h-20 overflow-auto text-xs text-red-500">{job.errors.map((item, index) => <div key={`${index}-${item}`}>{item}</div>)}</div>}
        </div>
      )}
    </section>
  )
}
