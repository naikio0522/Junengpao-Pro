import { useState, type DragEvent } from 'react'
import { api, type SubtitleExportResult } from '../api/client'

interface Props {
  onLog?: (line: string) => void
}

const VIDEO_PATTERN = /\.(mp4|mov|m4v|mkv|avi|webm)$/i

export function SubtitleConversionPanel({ onLog }: Props) {
  const [sourcePath, setSourcePath] = useState('')
  const [busy, setBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [result, setResult] = useState<SubtitleExportResult | null>(null)
  const [error, setError] = useState('')

  const chooseVideo = async () => {
    const selected = await window.electronAPI?.openFile([
      { name: '成片视频', extensions: ['mp4', 'mov', 'm4v', 'mkv', 'avi', 'webm'] },
    ])
    if (selected) { setSourcePath(selected); setResult(null); setError('') }
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    const path = [...event.dataTransfer.files].map(file => (file as File & { path?: string }).path || '')
      .find(value => VIDEO_PATTERN.test(value))
    if (!path) { setError('请拖入 MP4、MOV、MKV、AVI 或 WebM 成片文件'); return }
    setSourcePath(path)
    setResult(null)
    setError('')
  }

  const convert = async () => {
    if (busy || !sourcePath.trim()) return
    setBusy(true)
    setError('')
    try {
      const output = await api.exportSubtitleSrt(sourcePath.trim())
      setResult(output)
      onLog?.(`${output.status === 'existing' ? '已有字幕' : '字幕已生成'}：${output.srt_path}（${output.cue_count} 条）`)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      setError(message)
      onLog?.(`[成片转字幕失败] ${message}`)
    } finally {
      setBusy(false)
    }
  }

  return <section aria-label="成片转字幕" className="vm-card space-y-4 rounded-[16px] p-4">
    <div>
      <h2 className="text-[15px] font-semibold text-foreground">成片转字幕</h2>
      <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
        选择已有成片，离线识别语音并生成同名、带时间轴的 SRT 文件。原视频不会修改；生成后可在剪映中导入字幕文件。
      </p>
    </div>
    <div onDragEnter={event => { event.preventDefault(); setDragging(true) }}
      onDragOver={event => event.preventDefault()}
      onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false) }}
      onDrop={onDrop}
      className={`rounded-[14px] border border-dashed px-4 py-5 transition-colors ${dragging ? 'border-accent bg-accent/[0.10]' : 'border-border/[0.24] bg-background-elev/[0.35]'}`}>
      <p className="mb-3 text-[11px] text-muted-foreground">拖入成片视频，或选择文件</p>
      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="待转字幕成片路径" value={sourcePath}
          onChange={event => { setSourcePath(event.target.value); setResult(null); setError('') }}
          placeholder="选择或粘贴本地视频路径"
          className="h-9 min-w-[220px] flex-1 rounded-[9px] border border-border/[0.16] bg-background-elev px-3 font-mono text-[11px] text-foreground outline-none focus:border-accent/70" />
        <button type="button" onClick={() => void chooseVideo()} className="vm-action-secondary h-9 px-4 text-[11px]">选择成片</button>
        <button type="button" disabled={!sourcePath.trim() || busy} onClick={() => void convert()}
          className="vm-action-primary h-9 px-4 text-[11px] disabled:cursor-not-allowed disabled:opacity-45">
          {busy ? '离线识别中…' : '生成时间轴 SRT'}
        </button>
      </div>
    </div>
    {error && <p role="alert" className="rounded-[9px] border border-hot/30 bg-hot/[0.05] px-3 py-2 text-[11px] text-hot">{error}</p>}
    {result && <div aria-label="字幕生成结果" className="flex flex-wrap items-center justify-between gap-3 rounded-[12px] border border-ok/30 bg-ok/[0.05] px-3 py-2">
      <div className="min-w-0">
        <p className="text-[11px] font-semibold text-foreground">{result.status === 'existing' ? '同名 SRT 已存在，未覆盖' : `已生成 ${result.cue_count} 条字幕`}</p>
        <p className="break-all font-mono text-[10px] text-muted-foreground">{result.srt_path}</p>
      </div>
      <button type="button" onClick={() => void window.electronAPI?.openPath(result.folder)}
        className="vm-action-secondary h-8 shrink-0 px-3 text-[11px]">打开 SRT 文件夹</button>
    </div>}
  </section>
}
