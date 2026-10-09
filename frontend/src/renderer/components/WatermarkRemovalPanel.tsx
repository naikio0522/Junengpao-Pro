import { useEffect, useRef, useState, type DragEvent, type PointerEvent } from 'react'
import { api, type WatermarkDetection, type WatermarkRegion, type WatermarkRemovalJob } from '../api/client'

const LAST_JOB_KEY = 'vm-watermark-removal-task-id'
const VIDEO_PATTERN = /\.(mp4|mov|m4v|mkv|avi|webm)$/i

function fileName(path: string) {
  return path.split(/[\\/]/).at(-1) || path
}

function parentFolder(path: string) {
  const index = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'))
  return index < 0 ? '' : path.slice(0, index)
}

interface Props {
  onLog?: (line: string) => void
}

export function WatermarkRemovalPanel({ onLog }: Props) {
  const [paths, setPaths] = useState<string[]>([])
  const [outputDir, setOutputDir] = useState('')
  const [mode, setMode] = useState<'auto' | 'manual'>('auto')
  const [samplePath, setSamplePath] = useState('')
  const [detection, setDetection] = useState<WatermarkDetection | null>(null)
  const [region, setRegion] = useState<WatermarkRegion | null>(null)
  const [authorized, setAuthorized] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [pending, setPending] = useState(false)
  const [restoring, setRestoring] = useState(() => Boolean(localStorage.getItem(LAST_JOB_KEY)))
  const [job, setJob] = useState<WatermarkRemovalJob | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const seenLogs = useRef(0)
  const drawStart = useRef<{ x: number; y: number; pointerId: number } | null>(null)
  const active = restoring || pending || job?.status === 'pending' || job?.status === 'running'

  useEffect(() => {
    const taskId = localStorage.getItem(LAST_JOB_KEY)
    if (!taskId) return
    let alive = true
    api.getWatermarkRemovalJob(taskId)
      .then(current => {
        if (!alive) return
        seenLogs.current = current.log_lines.length
        setJob(current)
      })
      .catch(() => localStorage.removeItem(LAST_JOB_KEY))
      .finally(() => { if (alive) setRestoring(false) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!job?.task_id || !['pending', 'running'].includes(job.status)) return
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      try {
        const current = await api.getWatermarkRemovalJob(job.task_id)
        if (!alive) return
        setJob(current)
        for (const line of current.log_lines.slice(seenLogs.current)) onLog?.(line)
        seenLogs.current = current.log_lines.length
        if (current.status === 'pending' || current.status === 'running') timer = setTimeout(poll, 800)
      } catch (cause) {
        if (alive) setError(cause instanceof Error ? cause.message : String(cause))
      }
    }
    timer = setTimeout(poll, 400)
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [job?.task_id, job?.status, onLog])

  function addPaths(incoming: string[]) {
    const valid = incoming.map(value => value.trim()).filter(Boolean)
    setPaths(current => [...new Set([...current, ...valid])])
    if (!samplePath) setSamplePath(valid.find(value => VIDEO_PATTERN.test(value)) || '')
    setError('')
  }

  async function chooseVideos() {
    const selected = await window.electronAPI.openVideoFiles()
    if (selected?.length) addPaths(selected)
  }

  async function chooseFolder() {
    const selected = await window.electronAPI.openDirectory()
    if (typeof selected === 'string') addPaths([selected])
  }

  async function chooseSample() {
    const selected = await window.electronAPI.openVideoFiles()
    if (selected?.[0]) {
      setSamplePath(selected[0])
      setDetection(null)
      setRegion(null)
      setNotice('')
    }
  }

  async function chooseOutput() {
    const selected = await window.electronAPI.openDirectory(outputDir || undefined)
    if (typeof selected === 'string') setOutputDir(selected)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (active) return
    const dropped = Array.from(event.dataTransfer.files)
      .map(file => (file as File & { path?: string }).path || '')
      .filter(Boolean)
    if (dropped.length) addPaths(dropped)
    else setError('无法读取拖入的路径，请用“选择视频”或“选择文件夹”添加')
  }

  async function inspectSample() {
    if (!samplePath) {
      setError('请先选择一条样本视频；仅添加文件夹时，可从文件夹中选择一条用于预览')
      return
    }
    setDetecting(true)
    setError('')
    setNotice('')
    try {
      const result = await api.detectVideoWatermark(samplePath)
      setDetection(result)
      setRegion(result.region)
      setNotice(result.message)
      if (!result.region) setMode('manual')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setDetecting(false)
    }
  }

  function coordinates(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    return {
      x: Math.max(0, Math.min(detection!.width, Math.round((event.clientX - bounds.left) * detection!.width / bounds.width))),
      y: Math.max(0, Math.min(detection!.height, Math.round((event.clientY - bounds.top) * detection!.height / bounds.height))),
    }
  }

  function beginDraw(event: PointerEvent<HTMLDivElement>) {
    if (mode !== 'manual' || !detection || active || event.button !== 0) return
    const at = coordinates(event)
    drawStart.current = { ...at, pointerId: event.pointerId }
    event.currentTarget.setPointerCapture(event.pointerId)
    setRegion(null)
    setError('')
  }

  function continueDraw(event: PointerEvent<HTMLDivElement>) {
    const start = drawStart.current
    if (!start || !detection || start.pointerId !== event.pointerId) return
    const at = coordinates(event)
    setRegion({
      x: Math.min(start.x, at.x), y: Math.min(start.y, at.y),
      width: Math.abs(start.x - at.x), height: Math.abs(start.y - at.y),
    })
  }

  function finishDraw(event: PointerEvent<HTMLDivElement>) {
    const start = drawStart.current
    if (!start || start.pointerId !== event.pointerId) return
    continueDraw(event)
    drawStart.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }

  async function start() {
    if (!paths.length) { setError('先添加视频或文件夹'); return }
    if (!authorized) { setError('请先确认您拥有素材版权或处理授权'); return }
    if (mode === 'manual' && (!detection || !region || region.width < 4 || region.height < 4)) {
      setError('手动处理前，请先预览样本视频并拖动框选至少 4 像素的区域')
      return
    }
    if (mode === 'manual' && detection && region && region.width * region.height > detection.width * detection.height * .2) {
      setError('手动框选不能超过画面的 20%，请缩小范围')
      return
    }
    setPending(true)
    setError('')
    try {
      const created = await api.startWatermarkRemoval({
        input_paths: paths,
        output_dir: outputDir,
        mode,
        ...(mode === 'manual' && detection && region
          ? { region, reference_width: detection.width, reference_height: detection.height }
          : {}),
        authorized,
      })
      seenLogs.current = 0
      localStorage.setItem(LAST_JOB_KEY, created.task_id)
      setJob({ task_id: created.task_id, status: 'pending', progress: 0,
        current: 0, total: paths.length, output_files: [], errors: [], log_lines: [] })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setPending(false)
    }
  }

  async function stop() {
    if (!job) return
    try {
      await api.stopWatermarkRemovalJob(job.task_id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const outputFolder = job?.output_files[0] ? parentFolder(job.output_files[0]) : outputDir
  const drawnRegion = detection && region && region.width > 0 && region.height > 0 ? region : null

  return (
    <section className="space-y-4 rounded-md border border-border/20 bg-background-elev/50 p-4" aria-label="视频去水印">
      <div>
        <h2 className="text-sm font-semibold text-foreground">短视频一键去水印</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">适合固定在画面角落的小型静态角标。自动定位不可靠时，先预览并手动框选；原视频不会被覆盖。</p>
      </div>

      <div aria-label="拖入待去水印视频或文件夹"
        onDragEnter={event => { event.preventDefault(); if (!active) setDragging(true) }}
        onDragOver={event => event.preventDefault()}
        onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false) }}
        onDrop={handleDrop}
        className={`rounded-md border border-dashed p-5 text-center transition-colors ${dragging ? 'border-accent bg-accent/10' : 'border-border/40 bg-background/50'}`}>
        <div className="text-sm font-medium text-foreground">拖入视频或文件夹</div>
        <p className="mt-1 text-xs text-muted-foreground">可批量处理；也可以点击按钮选择</p>
        <div className="mt-3 flex justify-center gap-2">
          <button type="button" disabled={active} onClick={chooseVideos} className="rounded border border-border/40 px-3 py-1.5 text-xs text-foreground hover:border-accent disabled:opacity-50">选择视频</button>
          <button type="button" disabled={active} onClick={chooseFolder} className="rounded border border-border/40 px-3 py-1.5 text-xs text-foreground hover:border-accent disabled:opacity-50">选择文件夹</button>
        </div>
      </div>

      {paths.length > 0 && <div className="space-y-2">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>已添加 {paths.length} 个入口；文件夹内视频会自动查找</span>
          <button type="button" disabled={active} onClick={() => { setPaths([]); setSamplePath(''); setDetection(null); setRegion(null) }} className="text-accent hover:underline disabled:opacity-50">清空</button>
        </div>
        <div className="max-h-28 space-y-1 overflow-y-auto">
          {paths.map(path => <div key={path} className="flex items-center justify-between gap-2 rounded bg-background/60 px-2 py-1 text-xs">
            <span className="min-w-0 truncate text-foreground" title={path}>{fileName(path)}</span>
            <button type="button" disabled={active} aria-label={`移除 ${fileName(path)}`} onClick={() => {
              setPaths(current => current.filter(item => item !== path))
              if (samplePath === path) { setSamplePath(''); setDetection(null); setRegion(null); setNotice('') }
            }} className="shrink-0 text-muted-foreground hover:text-foreground disabled:opacity-50">移除</button>
          </div>)}
        </div>
      </div>}

      <div className="grid grid-cols-2 gap-2" role="group" aria-label="去水印方式">
        <button type="button" disabled={active} aria-pressed={mode === 'auto'} onClick={() => setMode('auto')}
          className={`rounded border p-2 text-left text-xs disabled:opacity-50 ${mode === 'auto' ? 'border-accent bg-accent/10 text-accent' : 'border-border/30 text-foreground hover:border-accent/60'}`}>
          <span className="block font-semibold">自动定位</span><span className="mt-1 block text-[10px] text-muted-foreground">每条视频分别识别静态角标</span>
        </button>
        <button type="button" disabled={active} aria-pressed={mode === 'manual'} onClick={() => setMode('manual')}
          className={`rounded border p-2 text-left text-xs disabled:opacity-50 ${mode === 'manual' ? 'border-accent bg-accent/10 text-accent' : 'border-border/30 text-foreground hover:border-accent/60'}`}>
          <span className="block font-semibold">手动框选</span><span className="mt-1 block text-[10px] text-muted-foreground">整批视频使用同一相对区域</span>
        </button>
      </div>

      <div className="space-y-2 rounded border border-border/20 bg-background/40 p-3">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="min-w-0 flex-1 truncate text-muted-foreground" title={samplePath}>样本：{samplePath ? fileName(samplePath) : '未选择（自动处理无需预览）'}</span>
          <button type="button" disabled={active} onClick={chooseSample} className="rounded border border-border/40 px-2 py-1 text-foreground hover:border-accent disabled:opacity-50">选择样本视频</button>
          <button type="button" disabled={active || detecting || !samplePath} onClick={inspectSample} className="rounded border border-accent/60 px-2 py-1 text-accent hover:bg-accent/10 disabled:opacity-50">{detecting ? '分析中…' : '预览并定位'}</button>
        </div>
        {notice && <p role="status" className="text-[11px] text-muted-foreground">{notice}{detection?.region ? `（置信度 ${Math.round(detection.confidence * 100)}%）` : ''}</p>}
        {detection && <div>
          <div onPointerDown={beginDraw} onPointerMove={continueDraw} onPointerUp={finishDraw} onPointerCancel={finishDraw}
            className={`relative mx-auto max-w-full overflow-hidden rounded border border-border/30 ${mode === 'manual' && !active ? 'cursor-crosshair touch-none' : ''}`}
            style={{ width: Math.min(520, detection.width), aspectRatio: `${detection.width} / ${detection.height}` }}
            aria-label={mode === 'manual' ? '在预览画面中拖动框选水印区域' : '视频样本画面'}>
            <img src={detection.preview} alt="视频样本画面" className="pointer-events-none h-full w-full object-fill" />
            {drawnRegion && <div className="pointer-events-none absolute border-2 border-accent bg-accent/20"
              style={{ left: `${100 * drawnRegion.x / detection.width}%`, top: `${100 * drawnRegion.y / detection.height}%`,
                width: `${100 * drawnRegion.width / detection.width}%`, height: `${100 * drawnRegion.height / detection.height}%` }} />}
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">{mode === 'manual' ? '在画面中拖动鼠标，框住需要修复的小区域。批量视频应使用相同构图。' : '自动定位的框仅供核对；开始处理时每条视频会重新识别。'}</p>
        </div>}
      </div>

      <label className="block text-xs text-foreground">输出到哪里（可选）
        <div className="mt-1 flex gap-2">
          <input value={outputDir} disabled={active} onChange={event => setOutputDir(event.target.value)} placeholder="默认：素材旁的“去水印输出”文件夹"
            className="min-w-0 flex-1 rounded border border-border/30 bg-background px-2 py-1.5 text-xs outline-none focus:border-accent disabled:opacity-50" />
          <button type="button" disabled={active} onClick={chooseOutput} className="rounded border border-border/40 px-3 py-1.5 text-xs hover:border-accent disabled:opacity-50">浏览</button>
        </div>
      </label>

      <label className="flex items-start gap-2 text-xs leading-5 text-foreground">
        <input type="checkbox" checked={authorized} onChange={event => setAuthorized(event.target.checked)} className="mt-1" />
        <span>我拥有这些视频的版权或已取得处理授权</span>
      </label>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={active || !paths.length} onClick={start} className="rounded bg-accent px-4 py-2 text-xs font-semibold text-background hover:opacity-90 disabled:opacity-50">{pending ? '正在创建任务…' : '开始去水印'}</button>
        {active && job && <button type="button" onClick={stop} className="rounded border border-border/40 px-4 py-2 text-xs text-foreground hover:border-accent">停止</button>}
      </div>
      {error && <p role="alert" className="rounded bg-red-500/10 p-2 text-xs text-red-500">{error}</p>}

      {job && <div className="space-y-2 rounded-md border border-border/20 bg-background/50 p-3 text-xs">
        <div className="flex justify-between gap-2"><span className="text-foreground">{job.status === 'completed' ? '处理完成' : job.status === 'failed' ? '处理失败' : job.status === 'stopped' ? '已停止' : '正在处理视频'}</span><span className="text-muted-foreground">{job.current}/{job.total} · {job.progress}%</span></div>
        <div role="progressbar" aria-valuenow={job.progress} aria-valuemin={0} aria-valuemax={100} className="h-1.5 overflow-hidden rounded-full bg-border/30"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} /></div>
        {job.log_lines.length > 0 && <div className="max-h-36 space-y-1 overflow-y-auto rounded border border-border/20 bg-background/60 p-2 text-[11px] leading-5 text-muted-foreground" aria-live="polite">
          {job.log_lines.slice(-30).map((line, index) => <div key={`${index}-${line}`}>{line}</div>)}
        </div>}
        {job.output_files.length > 0 && <div className="space-y-1"><span className="text-foreground">已生成 {job.output_files.length} 个视频</span>{job.output_files.map(file => <button key={file} type="button" title={file} onClick={() => window.electronAPI.openPath(file)} className="block max-w-full truncate text-accent hover:underline">{fileName(file)}</button>)}</div>}
        {outputFolder && <button type="button" onClick={() => window.electronAPI.openPath(outputFolder)} className="text-accent hover:underline">打开输出文件夹</button>}
        {job.errors.length > 0 && <div className="space-y-1 text-red-500">{job.errors.map((message, index) => <div key={`${index}-${message}`}>{message}</div>)}</div>}
      </div>}
      <p className="text-[11px] leading-4 text-muted-foreground">局部修复会模糊所选画面区域，复杂或移动水印不保证无痕；请查看成片后再使用。</p>
    </section>
  )
}
