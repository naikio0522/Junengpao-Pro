import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { useStore } from '../store'
import { api, PreflightReportItem, SpeechLogicPreview, TaskStatus } from '../api/client'
import { speechLogicConfigError } from '../speechLogic'
import { Checkbox } from './ui/checkbox'
import { Slider } from './ui/slider'
import { SubtitlePreview } from './SubtitlePreview'
import { AssetCard } from './AssetCard'
import { FeatureHelp } from './FeatureHelp'
import { ContactMe } from './ContactMe'
import { SponsorMe } from './SponsorMe'
import { AccountEntry } from './AccountEntry'
import { CheckUpdatesButton } from './CheckUpdatesButton'
import { StandaloneVariantPanel } from './StandaloneVariantPanel'
import { WorkflowSegmentedControl, type WorkflowMode } from './WorkflowSegmentedControl'

const RESOLUTION_PRESETS = [
  { label: '1080P', value: '1080*1920' },
  { label: '2K', value: '1440*2560' },
]

const VARIANT_STRENGTH_INFO = {
  mild: {
    label: '轻度',
    description: '变化最小，优先保持原画面构图与观感，适合对画质和主体位置敏感的成品。',
  },
  balanced: {
    label: '标准',
    description: '变化幅度与原画观感较均衡，适合大多数日常混剪任务，推荐默认使用。',
  },
  strong: {
    label: '增强',
    description: '裁切、色彩和帧混合变化更明显，适合能够接受较强画面变化的成品。',
  },
} as const

const LAST_BROWSE_DIRS_KEY = 'vm-last-browse-dirs'

const SUBTITLE_POSITION_PRESETS = [
  { label: '顶部', value: 12 },
  { label: '中央', value: 50 },
  { label: '底部', value: 88 },
] as const

const SUBTITLE_FONT_SIZE_PRESETS = [
  { label: '小', value: 4.4 },
  { label: '标准', value: 5.6 },
  { label: '大', value: 7.0 },
] as const

function readLastBrowseDirs(): Record<string, string> {
  try {
    const parsed = JSON.parse(localStorage.getItem(LAST_BROWSE_DIRS_KEY) || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

function parentDirectory(filePath: string): string {
  const value = filePath.trim()
  const separatorIndex = Math.max(value.lastIndexOf('\\'), value.lastIndexOf('/'))
  if (separatorIndex < 0) return ''
  if (separatorIndex === 2 && value[1] === ':') return value.slice(0, 3)
  return value.slice(0, separatorIndex)
}

function rememberBrowseDirectory(key: string, selectedPath: string, fileMode = false) {
  const remembered = readLastBrowseDirs()
  remembered[key] = fileMode ? parentDirectory(selectedPath) : selectedPath
  localStorage.setItem(LAST_BROWSE_DIRS_KEY, JSON.stringify(remembered))
}

function browseStartDirectory(key: string, currentPath: string, fileMode = false): string | undefined {
  const configured = currentPath.split(';').map((item) => item.trim()).filter(Boolean).at(-1) || ''
  const configuredDirectory = fileMode ? parentDirectory(configured) : configured
  return configuredDirectory || readLastBrowseDirs()[key] || undefined
}

// ─── Group ────────────────────────────────────────────────────────────────────
function Group({ title, children, className = '', action }: { title: React.ReactNode; children: React.ReactNode; className?: string; action?: React.ReactNode }) {
  return (
    <div className={className}>
      <div className="mb-2 flex min-h-5 items-center justify-between">
        <h2 className="vm-section-title uppercase">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  )
}

// ─── Text parameter row (compact) ─────────────────────────────────────────────
function ParamRow({ label, value, suffix, onChange, placeholder, disabled = false }: {
  label: string
  value: string | number
  suffix?: string
  placeholder?: string
  disabled?: boolean
  onChange: (v: string) => void
}) {
  return (
    <div className="flex items-center gap-2 h-7">
      <span className="w-[74px] shrink-0 inline-flex items-center text-[11px] text-muted-foreground">{label}<FeatureHelp topic={label} /></span>
      <input
        disabled={disabled}
        aria-label={label}
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        inputMode="decimal"
        className="h-7 min-w-0 flex-1 rounded-[4px] border border-border/[0.10] bg-background-elev px-2.5 font-mono text-[11px] text-foreground outline-none placeholder:text-muted-foreground/55 focus:border-accent/70 disabled:opacity-40"
      />
      {suffix && <span className="w-7 shrink-0 text-[10px] text-muted-foreground">{suffix}</span>}
    </div>
  )
}

function RangeParamRow({ label, min, max, onMinChange, onMaxChange, disabled = false }: {
  label: '首段' | '后段'
  min: string | number
  max: string | number
  onMinChange: (value: string) => void
  onMaxChange: (value: string) => void
  disabled?: boolean
}) {
  return <div className="flex h-7 min-w-0 items-center gap-1">
    <span className="inline-flex w-[74px] shrink-0 items-center text-[11px] text-muted-foreground">{label}<FeatureHelp topic={label} /></span>
    <input aria-label={`${label}最短秒数`} inputMode="decimal" disabled={disabled} value={String(min ?? '')}
      onChange={event => onMinChange(event.target.value)} title="最短秒数"
      className="h-7 w-0 min-w-[36px] flex-1 rounded-[4px] border border-border/[0.10] bg-background-elev px-1.5 text-center font-mono text-[11px] text-foreground outline-none focus:border-accent/70 disabled:opacity-40" />
    <span className="shrink-0 text-[10px] text-muted-foreground">–</span>
    <input aria-label={`${label}最长秒数`} inputMode="decimal" disabled={disabled} value={String(max ?? '')}
      onChange={event => onMaxChange(event.target.value)} title="最长秒数"
      className="h-7 w-0 min-w-[36px] flex-1 rounded-[4px] border border-border/[0.10] bg-background-elev px-1.5 text-center font-mono text-[11px] text-foreground outline-none focus:border-accent/70 disabled:opacity-40" />
    <span className="shrink-0 text-[10px] text-muted-foreground">s</span>
  </div>
}

function sourceName(sourceFile: string): string {
  return sourceFile.split(/[\\/]/).at(-1) || sourceFile
}

function SpeechPreviewCard({ item }: { item: PreflightReportItem }) {
  const preview = item.speech_logic_preview as SpeechLogicPreview
  const isFallback = !!preview.fallback
  const hookSegments = (preview.segments || []).filter(segment => segment.role === 'hook')
  const bodySegments = (preview.segments || []).filter(segment => segment.role === 'body')
  const sourceTypeLabel = { hook: 'Hook', body: 'Body', both: 'Hook / Body' }
  const sourceStatusLabel = { usable: '可用', review: '需复核', blocked: '未采用' }
  return (
    <section className="space-y-3 rounded-[5px] border border-accent/20 bg-accent/[0.025] p-3" aria-label={`${item.name}口播方案`}>
      <div className="text-[11px] font-semibold text-accent">{item.name} · 产品：{preview.product_id || '待识别'}</div>
      {item.ok ? <p className="text-[10px] leading-4 text-muted-foreground">
        {isFallback
          ? `${preview.fallback_mode === 'unranked_clip_level' ? '非分级混剪：同产品候选不按可用、需复核、未采用排序' : '保底混剪：优先同产品，依次选可用、需复核、未采用'}；使用 ${preview.hook_segment_count} 段 Hook（约 ${Number(preview.hook_duration_s || 0).toFixed(1)} 秒）接 ${bodySegments.length} 段 Body，预计可输出 ${preview.capacity} 条。`
          : `首个方案：Hook ${preview.hook_segment_count} 段话段合成一整段（约 ${Number(preview.hook_duration_s || 0).toFixed(1)} 秒），接 ${bodySegments.length} 段 Body；可编排 ${preview.capacity} 组。`}
      </p> : <p className="text-[10px] leading-4 text-hot">预检未通过：{item.message}</p>}
      {isFallback && <p className="rounded-[4px] border border-amber-500/25 bg-amber-500/[0.07] p-2 text-[10px] leading-4 text-amber-400">
        当前使用整段原声{preview.fallback_mode === 'unranked_clip_level' ? '非分级混剪' : '保底'}方案，未验证的转录、产品或价促内容可能进入成片。可继续渲染，但发布前请逐条人工复核画面和声音。
      </p>}
      {!!preview.segments?.length && (['hook', 'body'] as const).map((kind) => (
        <div key={kind} className="space-y-1">
          <h3 className="text-[10px] font-semibold text-foreground">{kind === 'hook' ? 'Hook 首段' : 'Body 后段'}{isFallback ? '：完整原片' : kind === 'hook' ? '：完整表达' : '：顺接话术'}</h3>
          {(kind === 'hook' ? hookSegments : bodySegments).map((segment, index) => (
            <div key={`${segment.source_file}-${segment.start_s}-${segment.end_s}-${index}`} className="rounded-[4px] border border-border/[0.09] bg-background/60 p-2">
              <p className="text-[11px] leading-[1.5] text-foreground">{index + 1}. {segment.text || (isFallback ? '未取得可靠台词；请复听原声' : '台词待确认')}</p>
              <p className="mt-1 break-all font-mono text-[9px] text-muted-foreground">
                {sourceName(segment.source_file)} · {Number(segment.start_s).toFixed(1)}–{Number(segment.end_s).toFixed(1)}s
                {' · 产品 '}{segment.product_id || preview.product_id || '待识别'}
              </p>
            </div>
          ))}
        </div>
      ))}
      {preview.transcript && !isFallback && <p className="rounded-[4px] border border-accent/15 bg-background/50 p-2 text-[10px] leading-[1.6] text-foreground/85">成片话术：{preview.transcript}</p>}
      {!!preview.warnings?.length && (
        <div className="space-y-1 text-[10px] leading-4 text-hot">
          {preview.warnings.map((warning, index) => <p key={index}>待复核：{warning}</p>)}
        </div>
      )}
      <div className="space-y-1 border-t border-border/[0.08] pt-2">
        <h3 className="text-[10px] font-semibold text-foreground">素材转录</h3>
        {(preview.transcripts || []).map((source, index) => (
          <details key={`${source.source_file}-${index}`} className="rounded-[4px] border border-border/[0.09] bg-background/60 p-2">
            <summary className="cursor-pointer break-all text-[10px] text-accent">
              {sourceTypeLabel[source.source_type] || source.source_type} · {sourceName(source.source_file)}
              {source.status && <span className={`ml-2 rounded-[3px] px-1.5 py-0.5 ${source.status === 'usable' ? 'bg-ok/10 text-ok' : source.status === 'review' ? 'bg-amber-500/10 text-amber-400' : 'bg-hot/10 text-hot'}`}>{sourceStatusLabel[source.status]}</span>}
            </summary>
            {(source.source_unit_count !== undefined || source.product_id) && (
              <p className="mt-2 text-[10px] text-muted-foreground">
                产品：{source.product_id || '待确认'}
                {source.source_unit_count !== undefined && ` · 候选 ${source.source_unit_count} 段，可用 ${source.usable_unit_count || 0} 段`}
              </p>
            )}
            {!!source.reasons?.length && <div className="mt-2 space-y-1 text-[10px] leading-4 text-hot">
              {source.reasons.map((reason, reasonIndex) => <p key={reasonIndex}>· {reason}</p>)}
            </div>}
            <p className="mt-2 break-words text-[10px] leading-[1.6] text-foreground/85">{source.text || '未获取到可用口播文字'}</p>
            <div className="mt-2 space-y-1">
              {(source.cues || []).map((cue, cueIndex) => (
                <p key={`${cue.start_s}-${cueIndex}`} className="text-[9px] leading-4 text-muted-foreground">
                  <span className="font-mono">{Number(cue.start_s).toFixed(1)}–{Number(cue.end_s).toFixed(1)}s</span> {cue.text}
                </p>
              ))}
            </div>
          </details>
        ))}
      </div>
    </section>
  )
}

function OverlapRateHelp() {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const closeIfOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', closeIfOutside)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeIfOutside)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  return (
    <div ref={containerRef} className="relative ml-1 inline-flex items-center normal-case tracking-normal">
      <button type="button" aria-label="打开重叠率说明" aria-expanded={open} aria-controls="overlap-rate-help" onClick={() => setOpen((value) => !value)}
        className="feature-help-trigger" title="重叠率说明">
        <span className="feature-help-glyph">?</span>
      </button>
      {open && (
        <div id="overlap-rate-help" role="dialog" aria-label="重叠率说明" className="absolute left-0 top-full z-50 mt-2 w-64 rounded-[6px] border border-accent/35 bg-background p-3 text-left normal-case tracking-normal shadow-[0_14px_32px_-16px_rgba(0,0,0,0.85)]">
          <p className="text-[11px] font-semibold text-foreground">原素材的裁切重叠率</p>
          <p className="mt-1 text-[10px] leading-4 text-muted-foreground">从同一份原素材连续取切片时，允许重复使用的时长占每个切片的比例。</p>
          <div className="mt-2 space-y-3 rounded-[4px] border border-muted-foreground/20 px-2 py-2 text-[10px] leading-4">
            <p className="text-muted-foreground">示例：每次裁切 10 秒，横轴为原素材时间 →</p>
            {[
              { label: '0%（填 0）：无重叠裁切', start: 10, end: 20, offset: '50%', detail: '裁切起点：0s → 10s；复用 0 秒' },
              { label: '30%（填 0.3）：有重叠裁切', start: 7, end: 17, offset: '35%', detail: '裁切起点：0s → 7s；复用 7–10s，共 3 秒' },
            ].map((example) => (
              <div key={example.start} className="space-y-1">
                <p className="font-semibold text-foreground">{example.label}</p>
                <div className="relative space-y-1" aria-label={example.detail}>
                  {example.start === 7 && <div aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-[35%] w-[15%] border-x border-dashed border-accent bg-accent/15" />}
                  <div className="relative w-1/2 rounded-[3px] bg-muted px-1 py-0.5 text-center text-foreground">切片 A · 0–10s</div>
                  <div className="relative w-1/2 rounded-[3px] bg-accent px-1 py-0.5 text-center text-background" style={{ marginLeft: example.offset }}>切片 B · {example.start}–{example.end}s</div>
                </div>
                <p className="text-muted-foreground">{example.detail}</p>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] leading-4 text-muted-foreground">比例越高，裁切起点越密，可选切片越多，原素材复用越多。此参数控制素材裁切，不改变成片的拼接方式。</p>
        </div>
      )}
    </div>
  )
}

function ReadonlyParamRow({ label, value, suffix }: { label: string; value: string | number; suffix?: string }) {
  return (
    <div className="flex items-center gap-2 h-7">
      <span className="w-14 shrink-0 text-[11px] text-muted-foreground">{label}</span>
      <span className="h-7 min-w-0 flex-1 rounded-[4px] border border-accent/20 bg-accent/[0.04] px-2.5 font-mono text-[11px] leading-7 text-accent">{value}</span>
      {suffix && <span className="w-9 shrink-0 text-[9px] text-muted-foreground">{suffix}</span>}
    </div>
  )
}

function BodyGroupRow({ index, group, onChange, onBrowse, onOpen }: {
  index: number
  group: { enabled: boolean; folder: string; clip_count: string | number; clip_duration: string | number; full_duration?: boolean }
  onChange: (patch: Partial<typeof group>) => void
  onBrowse: () => void
  onOpen: () => void
}) {
  return (
    <div className={`grid grid-cols-[42px_minmax(0,1fr)_54px_54px_96px] items-center gap-1 rounded-[5px] border px-1.5 py-1 ${group.enabled ? 'border-accent/28 bg-accent/[0.035]' : 'border-border/[0.07] bg-foreground/[0.012]'}`}>
      <button type="button" role="switch" aria-checked={group.enabled} onClick={() => onChange({ enabled: !group.enabled })}
        className={`h-6 rounded-[4px] border text-[9px] font-semibold ${group.enabled ? 'border-accent/55 bg-accent/12 text-accent' : 'border-border/[0.08] text-muted-foreground'}`}>
        组 {index + 1}
      </button>
      <div className="flex min-w-0 items-center gap-1">
        <input value={group.folder} onChange={(e) => onChange({ folder: e.target.value })} placeholder="Body 文件夹"
          className="h-6 min-w-0 flex-1 rounded-[4px] border border-border/[0.10] bg-background-elev px-2 font-mono text-[10px] text-foreground outline-none placeholder:text-muted-foreground/55 focus:border-accent/70" />
        <button type="button" onClick={onOpen} disabled={!group.folder} title="打开路径" className="h-6 w-6 shrink-0 rounded-[4px] border border-border/[0.08] text-[10px] text-muted-foreground hover:text-accent disabled:opacity-35">开</button>
        <button type="button" onClick={onBrowse} className="h-6 shrink-0 rounded-[4px] border border-border/[0.08] px-1.5 text-[9px] text-foreground/85 hover:border-accent/50 hover:text-accent">浏览</button>
      </div>
      <input type="number" min="1" step="1" value={String(group.clip_count)} onChange={(e) => onChange({ clip_count: e.target.value })} inputMode="numeric" title="片段数（至少 1）" placeholder="片段"
        className="h-6 min-w-0 rounded-[4px] border border-border/[0.10] bg-background-elev px-1.5 text-center font-mono text-[10px] text-foreground outline-none focus:border-accent/70" />
      <input disabled={group.full_duration} aria-label={`Body ${index + 1} 每段时长`} style={{ opacity: group.full_duration ? .4 : 1 }} type="number" min="0.5" step="0.1" value={String(group.clip_duration)} onChange={(e) => onChange({ clip_duration: e.target.value })} inputMode="decimal" title="每段时长（秒，至少 0.5）" placeholder="秒"
        className="h-6 min-w-0 rounded-[4px] border border-border/[0.10] bg-background-elev px-1.5 text-center font-mono text-[10px] text-foreground outline-none focus:border-accent/70" />
      <label className="flex shrink-0 cursor-pointer items-center gap-1 text-[9px] text-foreground/85">
        <Checkbox aria-label={`Body ${index + 1} 按原素材时长`} checked={!!group.full_duration}
          onCheckedChange={value => onChange({ full_duration: value === true })} />按原素材时长
      </label>
    </div>
  )
}

function getResolutionTag(value: string) {
  const normalized = value.toLowerCase().replace('*', 'x')
  const parts = normalized.split('x').map((v) => parseInt(v, 10))
  if (parts.length !== 2 || parts.some(Number.isNaN)) return '?'
  const pixels = parts[0] * parts[1]
  if (pixels >= 3840 * 2160 * 0.9) return '4K'
  if (pixels >= 2560 * 1440 * 0.9) return '2K'
  if (pixels >= 1920 * 1080 * 0.9) return '1080P'
  if (pixels >= 1280 * 720 * 0.9) return '720P'
  return '标清'
}

function parseFps(value: string | number) {
  const text = String(value).trim()
  const fraction = text.split('/').map(Number)
  if (fraction.length === 2 && fraction.every(Number.isFinite) && fraction[1] > 0) return fraction[0] / fraction[1]
  const parsed = Number(text)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 30
}

// ─── Task row ─────────────────────────────────────────────────────────────────
function TaskRow({ task }: { task: TaskStatus }) {
  const tone: Record<string, { bar: string; text: string; label: string }> = {
    pending:   { bar: 'bg-muted-foreground/40', text: 'text-muted-foreground', label: '等待' },
    running:   { bar: 'bg-accent',              text: 'text-accent',           label: '运行' },
    completed: { bar: 'bg-ok',                  text: 'text-ok',               label: '完成' },
    failed:    { bar: 'bg-hot',                 text: 'text-hot',              label: '失败' },
    stopped:   { bar: 'bg-muted-foreground/40', text: 'text-muted-foreground', label: '停止' },
  }
  const t = tone[task.status] || tone.pending
  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-border/[0.04] last:border-0">
      <span className={`w-8 shrink-0 text-[10px] font-mono ${t.text}`}>{t.label}</span>
      <span className="flex-1 text-[11px] text-foreground/85 truncate">{task.task_name}</span>
      {task.acceleration && task.status === 'running' && (
        <span
          className={`max-w-44 truncate text-[10px] ${task.acceleration_warning ? 'text-amber-400' : 'text-muted-foreground'}`}
          title={task.acceleration_warning || task.acceleration}
        >
          {task.acceleration_warning || task.acceleration}
        </span>
      )}
      {task.total > 0 && (
        <>
          <div className="w-20 h-px bg-foreground/[0.08] overflow-hidden relative">
            <div className={`absolute inset-y-0 left-0 ${t.bar} transition-all duration-500`} style={{ width: `${task.progress}%` }} />
          </div>
          <span className="text-[10px] font-mono text-muted-foreground tabular-nums w-14 text-right">
            {task.current}/{task.total} · {task.progress}%
          </span>
        </>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function SinglePage() {
  const { config, setConfig, tasks, logs, appendLog, clearLogs, addToast, scannedFiles } = useStore()
  const [isRunning, setIsRunning] = useState(false)
  const [rightTab, setRightTab] = useState<'log' | 'speech' | 'tasks' | 'output' | 'variant'>('log')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('vm-theme') as 'dark' | 'light') || 'dark')
  const [benchmarkRunning, setBenchmarkRunning] = useState(false)
  const [benchmarkProgress, setBenchmarkProgress] = useState(0)
  const [preflightRunning, setPreflightRunning] = useState(false)
  const [preflightProgress, setPreflightProgress] = useState<{ percent: number; message: string } | null>(null)
  const [speechPreflight, setSpeechPreflight] = useState<{ configKey: string; items: PreflightReportItem[] } | null>(null)
  const [completionNotice, setCompletionNotice] = useState<string | null>(null)
  const [workflow, setWorkflow] = useState<'mix' | 'dedup'>(() => localStorage.getItem('vm-workflow') === 'dedup' ? 'dedup' : 'mix')
  const [slideDirection, setSlideDirection] = useState<1 | -1>(1)
  const workflowMode: WorkflowMode = workflow === 'dedup' ? 'dedup' : config.selection_mode === 'speech_logic' ? 'speech_logic' : 'random'
  const chooseWorkflowMode = (mode: WorkflowMode) => {
    if (mode === workflowMode) return
    const order: WorkflowMode[] = ['speech_logic', 'random', 'dedup']
    setSlideDirection(order.indexOf(mode) > order.indexOf(workflowMode) ? 1 : -1)
    if (mode === 'dedup') {
      setWorkflow('dedup')
      return
    }
    setWorkflow('mix')
    if (mode === 'speech_logic') setConfig({ selection_mode: 'speech_logic', duration_mode: 'clips', body_mode: 'normal' })
    else setConfig({ selection_mode: 'random' })
    setRightTab('log')
  }
  const [hookRange, setHookRange] = useState<[number, number] | null>(null)
  const [hookRangeError, setHookRangeError] = useState(false)
  const [bodyRange, setBodyRange] = useState<[number, number] | null>(null)
  const [bodyRangeError, setBodyRangeError] = useState(false)
  useEffect(() => {
    if (config.selection_mode !== 'speech_logic' && rightTab === 'speech') setRightTab('log')
  }, [config.selection_mode, rightTab])
  useEffect(() => {
    let cancelled = false
    setBodyRange(null)
    setBodyRangeError(false)
    if (config.selection_mode === 'speech_logic') return
    const specs = config.body_mode === 'grouped'
      ? config.body_groups.filter(g => g.enabled).map(g => ({ full: g.full_duration, paths: [g.folder], count: Number(g.clip_count), min: Number(g.clip_duration), max: Number(g.clip_duration) }))
      : [{ full: config.body_full_duration, paths: config.body_dirs, count: Number(config.total_clips) - 1, min: Number(config.t_body_min), max: Number(config.t_body_max) }]
    if (!specs.some(s => s.full) || config.duration_mode === 'bgm') return
    const timer = window.setTimeout(async () => {
      try {
        let min = 0, max = 0
        for (const spec of specs) {
          if (!spec.full) { min += spec.count * spec.min; max += spec.count * spec.max; continue }
          const files = new Set<string>()
          for (const folder of spec.paths) {
            if (cancelled) return
            for (const file of (await api.scanDirectory(folder)).files) files.add(file)
          }
          const paths = [...files], durations: number[] = []
          for (let i = 0; i < paths.length && !cancelled; i += 4) {
            const results = await Promise.all(paths.slice(i, i + 4).map(file => api.probeFile(file).catch(() => null)))
            for (const r of results) if (r?.source_duration > 0) durations.push(r.source_duration)
          }
          durations.sort((a, b) => a - b)
          if (durations.length < spec.count) throw new Error('素材不足')
          min += durations.slice(0, spec.count).reduce((a, b) => a + b, 0)
          max += durations.slice(-spec.count).reduce((a, b) => a + b, 0)
        }
        if (!cancelled) setBodyRange([min, max])
      } catch { if (!cancelled) setBodyRangeError(true) }
    }, 400)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [config.selection_mode, config.body_mode, config.body_full_duration, config.body_dirs, config.body_groups, config.total_clips, config.t_body_min, config.t_body_max, config.duration_mode])
  const [bgmRange, setBgmRange] = useState<[number, number] | null>(null)
  const [bgmRangeError, setBgmRangeError] = useState(false)
  const previousDurationMode = useRef(config.duration_mode)
  useEffect(() => {
    if (previousDurationMode.current === 'bgm' && !config.bgm_dir.trim()) {
      addToast('未选择 BGM，已恢复按片段数量生成', 'info')
    }
    previousDurationMode.current = config.duration_mode
  }, [config.bgm_dir, config.duration_mode, addToast])
  useEffect(() => {
    let cancelled = false
    setBgmRange(null)
    setBgmRangeError(false)
    if (config.duration_mode !== 'bgm' || !config.bgm_dir.trim()) return
    const timer = window.setTimeout(async () => {
      try {
        const scan = await api.scanDirectory(config.bgm_dir, ['.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.mp4', '.mov', '.mkv', '.avi', '.webm'])
        const durations: number[] = []
        for (let i = 0; i < scan.files.length && !cancelled; i += 4) {
          const results = await Promise.all(scan.files.slice(i, i + 4).map(file => api.probeFile(file).catch(() => null)))
          for (const result of results) if (result?.audio_duration > 0) durations.push(result.audio_duration)
        }
        if (!cancelled) {
          setBgmRange(durations.length ? [Math.min(...durations), Math.max(...durations)] : null)
          setBgmRangeError(!durations.length)
        }
      } catch { if (!cancelled) setBgmRangeError(true) }
    }, 400)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [config.bgm_dir, config.duration_mode])
  useEffect(() => {
    let cancelled = false
    setHookRange(null)
    setHookRangeError(false)
    if (config.selection_mode === 'speech_logic' || !config.hook_full_duration || !config.hook_dir) return
    const timer = window.setTimeout(async () => {
      try {
        const scan = await api.scanDirectory(config.hook_dir)
        const durations: number[] = []
        // Probe in bounded batches so large folders do not flood the service.
        for (let i = 0; i < scan.files.length && !cancelled; i += 4) {
          const results = await Promise.all(scan.files.slice(i, i + 4).map(file => api.probeFile(file).catch(() => null)))
          for (const result of results) if (result?.source_duration > 0) durations.push(result.source_duration)
        }
        if (!cancelled) {
          setHookRange(durations.length ? [Math.min(...durations), Math.max(...durations)] : null)
          setHookRangeError(!durations.length)
        }
      } catch { if (!cancelled) setHookRangeError(true) }
    }, 400)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [config.selection_mode, config.hook_full_duration, config.hook_dir])
  const logRef = useRef<HTMLDivElement>(null)
  const followLogsRef = useRef(true)
  const logScrollTopRef = useRef(0)
  const taskStatusRef = useRef<Record<string, string>>({})

  useLayoutEffect(() => {
    if (!logs.length) {
      followLogsRef.current = true
      logScrollTopRef.current = 0
    }
    const panel = logRef.current
    if (!panel) return
    panel.scrollTop = followLogsRef.current ? panel.scrollHeight : logScrollTopRef.current
  }, [logs, rightTab])

  useEffect(() => {
    document.documentElement.classList.toggle('theme-light', theme === 'light')
    localStorage.setItem('vm-theme', theme)
  }, [theme])

  useEffect(() => {
    localStorage.setItem('vm-workflow', workflow)
  }, [workflow])

  useEffect(() => {
    tasks.forEach((task) => {
      const previous = taskStatusRef.current[task.task_id]
      if (previous && previous !== task.status && task.status === 'completed') {
        const partial = task.message?.includes('部分完成')
        addToast(partial ? `${task.task_name}：${task.message}` : `任务完成：${task.task_name}`, partial ? 'warning' : 'success')
        appendLog(`${partial ? '⚠️' : '✅'} ${new Date().toLocaleTimeString()} ${partial ? task.message : `任务完成：${task.task_name}`}`)
        if (!partial) {
          setCompletionNotice(task.task_name)
          window.setTimeout(() => setCompletionNotice(null), 6500)
        }
        setRightTab('output')
      } else if (previous && previous !== task.status && task.status === 'failed') {
        addToast(`${task.task_name}：${task.message || '渲染失败，请查看日志'}`, 'error')
        setRightTab('log')
      }
      taskStatusRef.current[task.task_id] = task.status
    })
  }, [tasks, addToast, appendLog])

  const running = tasks.filter(t => t.status === 'running').length
  const subtitleYPercent = Math.max(8, Math.min(92, Number(config.subtitle_y_percent) || 92))
  const subtitleFontSizePercent = Math.max(3, Math.min(9, Number(config.subtitle_font_size_percent) || 5.6))
  const enabledBodyGroups = config.body_groups.filter((group) => group.enabled)
  const fullBody = config.body_mode === 'grouped' ? enabledBodyGroups.some(group => group.full_duration) : !!config.body_full_duration
  const allFullBody = config.body_mode === 'grouped' ? enabledBodyGroups.length > 0 && enabledBodyGroups.every(group => group.full_duration) : !!config.body_full_duration
  const groupedClipCount = 1 + enabledBodyGroups.reduce((total, group) => total + Math.max(0, Math.floor(Number(group.clip_count) || 0)), 0)
  const coverFrameDuration = config.enable_random_cover && config.random_cover_mode === 'insert' ? 1 / parseFps(config.fps) : 0
  const groupedBodySeconds = config.body_mode === 'grouped'
    ? enabledBodyGroups.reduce((sum, group) => sum + Number(group.clip_count || 0) * Number(group.clip_duration || 0), 0)
    : 0
  const bodySecondsRange: [number, number] = config.body_mode === 'grouped'
    ? [groupedBodySeconds, groupedBodySeconds]
    : [Number(config.t_body_min) * Math.max(0, Number(config.total_clips) - 1), Number(config.t_body_max) * Math.max(0, Number(config.total_clips) - 1)]
  const effectiveHookRange = config.hook_full_duration ? hookRange : [Number(config.t_hook_min), Number(config.t_hook_max)]
  const audioDurationLabel = bgmRange && effectiveHookRange
    ? bgmRange.map((value, index) => ((config.apply_bgm_to_hook ? Math.max(value, effectiveHookRange[index]) : value + effectiveHookRange[index]) + coverFrameDuration).toFixed(3)).join('–') + 's'
    : bgmRangeError || hookRangeError ? '时长读取失败，请预检' : '读取音频时长中…'
  const fullBodyDurationLabel = bodyRange && effectiveHookRange ? bodyRange.map((v, i) => (v + effectiveHookRange[i] + coverFrameDuration).toFixed(3)).join('–') + 's' : bodyRangeError ? '素材不足或读取失败，请预检' : '读取原片时长中…'
  const durationLabel = config.duration_mode === 'bgm' ? audioDurationLabel : fullBody ? fullBodyDurationLabel : config.hook_full_duration
    ? hookRange ? hookRange.map((value, index) => (value + bodySecondsRange[index] + coverFrameDuration).toFixed(coverFrameDuration ? 3 : 1)).join('–') + 's'
      : !config.hook_dir ? '请选择 Hook 目录' : hookRangeError ? '时长读取失败' : '读取时长中…'
    : [Number(config.t_hook_min) + bodySecondsRange[0], Number(config.t_hook_max) + bodySecondsRange[1]]
      .map(value => (value + coverFrameDuration).toFixed(coverFrameDuration ? 3 : 1)).join('–') + 's'
  const finishedHookMode = !config.apply_bgm_to_hook && !config.apply_voice_to_hook && !config.apply_srt_to_hook && !config.apply_watermark_to_hook
  const visibleSpeechPreviews = config.selection_mode === 'speech_logic' && speechPreflight?.configKey === JSON.stringify(config)
    ? speechPreflight.items.filter(item => item.speech_logic_preview)
    : []
  const allOutputItems = tasks.flatMap(t => t.output_files.map((file) => ({
    file,
    elapsed: t.output_elapsed?.[file],
  })))
  const setParam = (key: string, raw: string) => setConfig({ [key]: raw } as any)
  const toggleFinishedHookMode = () => {
    const enabled = !finishedHookMode
    setConfig({
      apply_bgm_to_hook: !enabled,
      apply_voice_to_hook: !enabled,
      apply_srt_to_hook: !enabled,
      apply_watermark_to_hook: !enabled,
      vol_hook_orig: enabled ? 100 : config.vol_orig,
    })
    addToast(enabled ? '已启用成品 Hook 保护' : '已恢复全片效果', 'success')
  }
  const splitPathList = (raw: string) =>
    raw.split(';').map(p => p.trim()).filter(Boolean)
  const openConfiguredPath = (raw: string, fileMode = false) => {
    const paths = splitPathList(raw)
    paths.forEach((p) => {
      if (!p) return
      const target = fileMode ? p.replace(/[\\/][^\\/]*$/, '') : p
      window.electronAPI?.openPath(target)
    })
  }
  const isFilePath = (value: string) => /\.(mp3|wav|m4a|aac|flac|ogg|opus|mp4|mov|mkv|avi|webm)$/i.test(value.trim())
  const ensureRunConfig = () => {
    if (!config.hook_dir.trim()) {
      addToast('请选择 Hook 首段视频或文件夹（必填）', 'warning')
      return null
    }
    if (!config.base_out_dir.trim()) {
      addToast('请选择输出目录（必填）', 'warning')
      return null
    }
    if (config.body_mode === 'normal' && !config.body_dirs.some(path => path.trim())) {
      addToast('请选择 Body 后段文件夹（必填）', 'warning')
      return null
    }
    if (config.selection_mode === 'speech_logic') {
      const error = speechLogicConfigError(config)
      if (error) { addToast(error, 'warning'); return null }
    }
    const bodyDirs = config.body_dirs
    if (config.selection_mode === 'random') {
      for (const [label, min, max, full] of [
        ['首段', config.t_hook_min, config.t_hook_max, config.hook_full_duration],
        ['后段', config.t_body_min, config.t_body_max, config.body_full_duration || config.body_mode === 'grouped'],
      ] as const) {
        if (full) continue
        const lower = Number(min), upper = Number(max)
        if (!Number.isFinite(lower) || !Number.isFinite(upper) || lower < 0.5 || upper < lower) {
          addToast(`${label}秒数范围须 ≥ 0.5，且最长秒数不能小于最短秒数`, 'warning')
          return null
        }
      }
    }
    if (config.body_mode === 'grouped') {
      const missingFolder = enabledBodyGroups.some((group) => !group.folder.trim())
      const invalidGroup = enabledBodyGroups.some((group) => !Number.isInteger(Number(group.clip_count)) || Number(group.clip_count) < 1 || (!group.full_duration && Number(group.clip_duration) < 0.5))
      if (enabledBodyGroups.length === 0 || missingFolder || invalidGroup) {
        addToast('每个启用组需有文件夹、整数片段数和至少 0.5 秒时长', 'warning')
        return null
      }
    }
    const parsedConcurrency = Number(config.concurrent_tasks)
    const concurrentTasks = Number.isFinite(parsedConcurrency) && parsedConcurrency >= 1
      ? Math.floor(parsedConcurrency)
      : 3
    if (concurrentTasks !== config.concurrent_tasks) {
      setConfig({ concurrent_tasks: concurrentTasks })
    }
    return {
      ...config,
      body_dirs: bodyDirs,
      concurrent_tasks: concurrentTasks,
      total_clips: config.body_mode === 'grouped' ? groupedClipCount : config.total_clips,
    }
  }

  const browse = async (key: string, multi = false) => {
    if (!window.electronAPI) { addToast('请在 Electron 中运行', 'warning'); return }
    const currentPath = key === 'body_dirs'
      ? config.body_dirs.join(';')
      : String(config[key as keyof typeof config] || '')
    const selected = await window.electronAPI.openDirectory(
      browseStartDirectory(key, currentPath, isFilePath(currentPath)),
      multi,
    )
    if (!selected) return
    const selectedPaths = Array.isArray(selected) ? selected : [selected]
    if (selectedPaths.length === 0) return
    rememberBrowseDirectory(key, selectedPaths.at(-1)!)
    if (key === 'body_dirs') {
      const seen = new Set(config.body_dirs.map((item) => item.toLocaleLowerCase()))
      const appended = [...config.body_dirs]
      let addedCount = 0
      selectedPaths.forEach((selectedPath) => {
        const normalized = selectedPath.toLocaleLowerCase()
        if (seen.has(normalized)) return
        seen.add(normalized)
        appended.push(selectedPath)
        addedCount += 1
      })
      setConfig({ body_dirs: appended })
      addToast(addedCount > 0
        ? `已加入 ${addedCount} 个 Body 文件夹，共 ${appended.length} 个`
        : '所选 Body 文件夹已存在', addedCount > 0 ? 'success' : 'info')
    } else if (key === 'srt_dir') {
      setConfig({ srt_dir: selectedPaths[0], enable_srt: true })
      addToast('字幕目录已选择并自动开启', 'success')
    } else {
      setConfig({ [key]: selectedPaths[0] } as any)
    }
  }

  const browseHookVideo = async () => {
    if (!window.electronAPI) { addToast('请在 Electron 中运行', 'warning'); return }
    const selected = await window.electronAPI.openVideoFiles(
      browseStartDirectory('hook_dir', config.hook_dir, isFilePath(config.hook_dir)),
      ['mp4', 'mov'],
    )
    if (!selected?.length) return
    const seen = new Set<string>()
    const paths = selected.filter((path) => {
      const key = path.toLocaleLowerCase()
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
    if (paths.some(path => path.includes(';'))) {
      addToast('所选视频路径含分号，无法作为多选素材使用；请先重命名路径', 'warning')
      return
    }
    rememberBrowseDirectory('hook_dir', paths.at(-1)!, true)
    setConfig({ hook_dir: paths.join('; ') })
    addToast(`已选择 ${paths.length} 个 Hook 视频`, 'success')
  }

  const updateBodyGroup = (index: number, patch: Partial<(typeof config.body_groups)[number]>) => {
    setConfig({ body_groups: config.body_groups.map((group, groupIndex) => groupIndex === index ? { ...group, ...patch } : group) })
  }

  const browseBodyGroup = async (index: number) => {
    if (!window.electronAPI) { addToast('请在 Electron 中运行', 'warning'); return }
    const group = config.body_groups[index]
    const key = `body_group_${index}`
    const selected = await window.electronAPI.openDirectory(browseStartDirectory(key, group.folder), false)
    if (!selected || Array.isArray(selected)) return
    rememberBrowseDirectory(key, selected)
    updateBodyGroup(index, { folder: selected })
  }

  const browseFile = async (key: string) => {
    if (!window.electronAPI) { addToast('请在 Electron 中运行', 'warning'); return }
    const currentPath = String(config[key as keyof typeof config] || '')
    const selectedPath = await window.electronAPI.openFile(
      [{ name: 'Images', extensions: ['png', 'gif', 'jpg'] }],
      browseStartDirectory(key, currentPath, true),
    )
    if (selectedPath) {
      rememberBrowseDirectory(key, selectedPath, true)
      setConfig({ [key]: selectedPath } as any)
    }
  }

  const startRender = async () => {
    const runConfig = ensureRunConfig()
    if (!runConfig) return
    setIsRunning(true)
    try {
      const res = await api.createTask(runConfig)
      addToast('任务已启动', 'success')
      appendLog(`▸ ${new Date().toLocaleTimeString()} 任务 ${res.task_id} 已派发`)
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    } finally { setIsRunning(false) }
  }

  const preFlight = async () => {
    const runConfig = ensureRunConfig()
    if (!runConfig || preflightRunning) return
    setPreflightRunning(true)
    setPreflightProgress({ percent: 0, message: '准备检查素材' })
    setSpeechPreflight(null)
    setRightTab('log')
    clearLogs()
    appendLog('>>> [预检] 正在扫描素材，请稍候...')
    try {
      const res = await api.preflight(runConfig, (percent, message) => {
        setPreflightProgress({ percent, message })
      })
      setPreflightProgress({ percent: 100, message: '全部素材预检完成' })
      clearLogs()
      ;(res.report || []).forEach((item) => appendLog(`${!item.ok ? '❌' : item.speech_logic_preview?.fallback ? '⚠️' : '✅'} [${item.name}] ${item.message}`))
      const preflightSummary = runConfig.selection_mode !== 'speech_logic'
        ? '按当前混剪素材与时长设置执行。'
        : runConfig.no_fallback_mix
          ? '非分级混剪不按可用、需复核、未采用排序，已识别的不同产品不混用。'
          : '同产品优先，需复核和未采用素材仅在优选不足时补入。'
      appendLog(`>>> 预检完成。预计可输出: ${res.capacity} 条；${preflightSummary}`)
      const previewItems = (res.report || []).filter(item => item.speech_logic_preview)
      if (runConfig.selection_mode === 'speech_logic' && previewItems.length) {
        setSpeechPreflight({ configKey: JSON.stringify(config), items: previewItems })
        setRightTab('speech')
      }
      addToast(res.ok && previewItems.some(item => item.speech_logic_preview?.fallback) ? '预检完成：可渲染，成片需人工复核' : '预检完成', res.ok ? 'success' : 'error')
    }
    catch (e: any) {
      const message = e?.message || '预检请求失败'
      setPreflightProgress((previous) => ({ percent: previous?.percent ?? 0, message: '预检失败' }))
      appendLog(`[预检失败] ${message}`)
      addToast(message, 'error')
    } finally {
      setPreflightRunning(false)
    }
  }

  const benchmark = async () => {
    const runConfig = ensureRunConfig()
    if (!runConfig || benchmarkRunning) return

    setBenchmarkRunning(true)
    setBenchmarkProgress(1)
    appendLog('>>> [压测] 正在测试 1%')
    addToast('智能压测已开始', 'info')

    const timer = window.setInterval(() => {
      setBenchmarkProgress((p) => {
        const next = Math.min(95, p + 7)
        if (next % 14 === 0 || next === 95) appendLog(`>>> [压测] 正在测试 ${next}%`)
        return next
      })
    }, 900)

    try {
      const res = await api.benchmark(runConfig)
      if (res.error) { addToast(res.error, 'error'); return }
      setBenchmarkProgress(100)
      appendLog('>>> [压测] 智能压测完成')
      Object.values(res.results || {}).forEach((item: any) => {
        appendLog(`    - ${item.concurrent} 路并发总耗时: ${item.total_time} 秒，单视频平均: ${item.avg_per_video} 秒`)
      })
      appendLog(`✅ [压测完成] 最优节点为 ${res.best_concurrent} 路并发`)
      setConfig({ concurrent_tasks: res.best_concurrent })
      addToast(`最优并发 ${res.best_concurrent} 路`, 'success')
    } catch (e: any) {
      addToast(e.message, 'error')
    } finally {
      window.clearInterval(timer)
      window.setTimeout(() => {
        setBenchmarkRunning(false)
        setBenchmarkProgress(0)
      }, 800)
    }
  }

  const clearHistory = async () => {
    try {
      await api.clearHistory()
      addToast('历史记录已清理', 'success')
      appendLog(`▸ ${new Date().toLocaleTimeString()} 历史记录已清理`)
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    }
  }

  const stopRunning = async () => {
    const activeTasks = tasks.filter((t) => t.status === 'running' || t.status === 'pending')
    if (activeTasks.length === 0) {
      addToast('当前没有运行中的任务', 'info')
      return
    }
    try {
      await Promise.all(activeTasks.map((t) => api.stopTask(t.task_id)))
      appendLog(`▸ ${new Date().toLocaleTimeString()} 已发送停止指令`)
      addToast('停止指令已发送', 'success')
    } catch (e: any) {
      addToast(e.message, 'error')
      appendLog(`[错误] ${e.message}`)
    }
  }

  // ──────────────────────────────────────────────────────────────────────────

  return (
    <div className="ui-shell vm-workspace flex min-w-0 flex-col overflow-hidden">
      {/* macOS hidden-titlebar drag region */}
      <div className="h-3 w-full shrink-0" style={{ WebkitAppRegion: 'drag' } as React.CSSProperties} />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col px-4 pb-3">
        {/* Header */}
        <div className="vm-topbar flex shrink-0 flex-wrap items-center justify-between gap-x-5 gap-y-2 rounded-[18px] px-4 py-2.5 mb-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            <h1 className="shrink-0 text-[16px] font-bold tracking-tight text-foreground">巨能跑<span className="text-accent">pro</span>版 <span className="ml-1 rounded-full border border-accent/20 bg-accent/[0.08] px-1.5 py-0.5 text-[10px] font-medium text-accent">v0.1.5</span></h1>
            <FeatureHelp tutorial />
            <ContactMe />
            <SponsorMe />
            <CheckUpdatesButton />
          </div>
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              className="vm-action-secondary h-8 px-3 text-[11px]"
            >
              {theme === 'dark' ? 'Light' : 'Dark'}
            </button>
            <AccountEntry />
            <span className="min-w-16 rounded-full border border-border/[0.14] bg-background/[0.25] px-2.5 py-1 text-center text-[11px] font-mono text-muted-foreground">
              {running > 0 ? (
                <span className="text-accent inline-flex items-center gap-1.5">
                  <span className="w-1 h-1 rounded-full bg-accent animate-pulse-dot" />
                  {running} 运行中
                </span>
              ) : '就绪'}
            </span>
          </div>
        </div>

        <section aria-label="你想让我怎么做？" className="vm-task-panel mb-3 w-full min-w-0 shrink-0 rounded-[18px] px-4 py-3">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-[15px] font-semibold tracking-tight text-foreground">你想让我怎么做？</h2>
            <span className="text-[11px] text-muted-foreground">① 选择任务　② 添加素材　③ 调整参数并开始</span>
          </div>
          <WorkflowSegmentedControl value={workflowMode} onChange={chooseWorkflowMode} />
          <p key={workflowMode} className="vm-mode-copy mt-2 text-[11px] leading-5 text-muted-foreground">
            {workflow === 'dedup'
              ? '已有成片直接拖入或选择视频、文件夹；不需要 Hook / Body。原视频保留，处理结果另存。'
              : config.selection_mode === 'speech_logic'
                ? '请先整理好对应的完整Hook首段和Body后段的文件夹内容；核对产品是否一致、台词是否通顺。该板块目前还在测试中，使用过程如遇到问题请点击上方“联系我”获取联系方式'
                : '按设定时长从 Hook 和 Body 素材中抽取片段并拼接；适合不需要台词语义衔接的镜头。'}
          </p>
          {workflow === 'mix' && config.selection_mode === 'speech_logic' && (
            <div className="vm-mode-copy mt-2 grid grid-cols-1 gap-2">
              <label className="space-y-1 text-[11px] text-muted-foreground">
                <span>产品（可选）</span>
                <input aria-label="产品（可选）" value={config.semantic_sku || ''}
                  onChange={(event) => setConfig({ semantic_sku: event.target.value })}
                  placeholder="例如：色修牙膏、修护牙膏、99牙膏"
                  className="h-8 w-full rounded-[9px] border border-border/[0.16] bg-background-elev px-3 text-[11px] text-foreground outline-none placeholder:text-muted-foreground/55 focus:border-accent/70" />
              </label>
            </div>
          )}
          {workflow === 'mix' && config.selection_mode === 'speech_logic' && (config.voice_dir?.trim() || config.enable_srt) && (
            <p className="mt-2 text-[10px] text-hot">口播逻辑保留原声：请清空外部配音并关闭随机字幕后再预检。</p>
          )}
        </section>

        <div className="vm-workflow-stage min-h-0 min-w-0 flex-1" style={{ '--vm-slide-direction': slideDirection } as React.CSSProperties}>
          <div data-testid="workflow-pane-dedup" aria-hidden={workflow !== 'dedup'}
            className={`vm-workflow-pane ${workflow === 'dedup' ? 'is-active' : ''}`}>
            <div className="min-h-0 min-w-0 flex-1 overflow-y-auto pr-1">
            <StandaloneVariantPanel onLog={appendLog} />
            </div>
          </div>
          <div data-testid="workflow-pane-mix" aria-hidden={workflow !== 'mix'}
            className={`vm-workflow-pane vm-mix-${config.selection_mode} ${workflow === 'mix' ? 'is-active' : ''}`}>
        {/* TWO COLUMNS: left controls / right telemetry */}
        <div className="vm-mix-grid grid min-h-0 min-w-0 w-full flex-1 grid-cols-1 content-start gap-3.5 overflow-y-auto xl:grid-cols-[minmax(0,0.68fr)_minmax(0,0.32fr)] xl:content-stretch xl:overflow-hidden">

          {/* ─── LEFT: configuration ─── */}
          <div className="flex min-w-0 flex-col gap-3 xl:min-h-0 xl:overflow-y-auto xl:pr-1">

            {/* Sources — long path inputs */}
            <Group title={<span>素材<FeatureHelp topic="finished" title="成品 Hook" /></span>} action={(
              <button
                type="button"
                onClick={toggleFinishedHookMode}
                title="关闭 BGM、配音、字幕、水印对 Hook 的二次处理，并将 Hook 原声设为 100%"
                className={`h-7 rounded-[8px] border px-2.5 text-[10px] font-semibold transition-colors ${
                  finishedHookMode
                    ? 'border-accent bg-accent text-background'
                    : 'border-border/[0.10] bg-foreground/[0.02] text-muted-foreground hover:border-accent/60 hover:text-accent'
                }`}
              >
                成品 Hook
              </button>
            )}>
              <div className="grid grid-cols-1 gap-2">
                <AssetCard kind="hook" label="Hook 首段" value={config.hook_dir} count={splitPathList(config.hook_dir).length > 1 ? splitPathList(config.hook_dir).length : scannedFiles.hook?.count} required pickAction="文件夹" secondaryAction="视频（可多选）" onSecondaryAction={browseHookVideo}
                  onChange={(v) => setConfig({ hook_dir: v })}
                  onOpen={() => {
                    const firstHook = splitPathList(config.hook_dir)[0] || ''
                    openConfiguredPath(firstHook, isFilePath(firstHook))
                  }}
                  onBrowse={() => browse('hook_dir')}        onClear={() => setConfig({ hook_dir: '' })} />
                <div className="space-y-2 rounded-[14px] border border-border/[0.14] bg-background-elev/[0.55] p-2">
                  <div className="flex items-center justify-between gap-2 px-0.5">
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-medium text-foreground/90">Body 后段{config.body_mode === 'grouped' && <span className="ml-0.5 font-bold text-hot" aria-label="必填">*</span>}<FeatureHelp topic="body" title="Body 分组" /></span>
                      <div className="flex items-center rounded-[9px] border border-border/[0.16] bg-background/30 p-0.5">
                        {(['normal', 'grouped'] as const).map((mode) => (
                          <button key={mode} type="button" disabled={config.selection_mode === 'speech_logic' && mode === 'grouped'} onClick={() => setConfig({ body_mode: mode })}
                            className={`h-6 rounded-[7px] px-2.5 text-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${config.body_mode === mode ? 'bg-accent text-background' : 'text-muted-foreground hover:text-accent'}`}>
                            {mode === 'normal' ? '普通' : '分组'}
                          </button>
                        ))}
                      </div>
                    </div>
                    {config.body_mode === 'grouped' && <span className="font-mono text-[9px] text-accent">共 {groupedClipCount} 段 · {durationLabel}</span>}
                  </div>
                  {config.body_mode === 'normal' ? (
                    <AssetCard kind="body" label="文件夹" value={config.body_dirs.join('; ')} count={scannedFiles.body?.count} pickAction="追加" required
                      onChange={(v) => setConfig({ body_dirs: splitPathList(v) })}
                      onOpen={() => openConfiguredPath(config.body_dirs.join('; '))}
                      onBrowse={() => browse('body_dirs', true)} onClear={() => setConfig({ body_dirs: [] })} />
                  ) : (
                    <div className="space-y-1">
                      <div className="grid grid-cols-[42px_minmax(0,1fr)_54px_54px_96px] gap-1 px-1.5 text-center text-[9px] text-muted-foreground">
                        <span>开关</span><span>每组文件夹（按组顺序固定）</span><span>片段</span><span>时长 / 秒</span><span>完整原片</span>
                      </div>
                      {config.body_groups.map((group, index) => (
                        <BodyGroupRow key={index} index={index} group={group}
                          onChange={(patch) => updateBodyGroup(index, patch)}
                          onBrowse={() => browseBodyGroup(index)}
                          onOpen={() => openConfiguredPath(group.folder)} />
                      ))}
                      <p className="px-1 text-[9px] text-muted-foreground">各组内随机抽取，组与组按 1→4 顺序拼接。</p>
                    </div>
                  )}
                </div>
                <AssetCard kind="bgm"       label="BGM"  value={config.bgm_dir}              count={scannedFiles.bgm?.count} pickAction="目录"
                  onChange={(v) => setConfig({ bgm_dir: v })}
                  onOpen={() => openConfiguredPath(config.bgm_dir, isFilePath(config.bgm_dir))}
                  onBrowse={() => browse('bgm_dir')}
                  bodyOnly={!config.apply_bgm_to_hook} onBodyOnlyChange={(v) => setConfig({ apply_bgm_to_hook: !v })}
                  onClear={() => setConfig({ bgm_dir: '' })} />
                <AssetCard kind="voice"     label="配音"      value={config.voice_dir || ''}
                  onChange={(v) => setConfig({ voice_dir: v })}
                  onOpen={() => openConfiguredPath(config.voice_dir || '')}
                  bodyOnly={!config.apply_voice_to_hook} onBodyOnlyChange={(v) => setConfig({ apply_voice_to_hook: !v })}
                  onBrowse={() => browse('voice_dir')}       onClear={() => setConfig({ voice_dir: '' })} />
                <AssetCard kind="srt"       label="字幕"      value={config.srt_dir || ''}
                  onChange={(v) => setConfig({ srt_dir: v, enable_srt: Boolean(v.trim()) })}
                  onOpen={() => openConfiguredPath(config.srt_dir || '')}
                  enabled={config.enable_srt}
                  onEnabledChange={(enabled) => {
                    if (enabled && !config.srt_dir?.trim()) {
                      addToast('请先选择字幕目录', 'warning')
                      return
                    }
                    setConfig({ enable_srt: enabled })
                  }}
                  bodyOnly={!config.apply_srt_to_hook} onBodyOnlyChange={(v) => setConfig({ apply_srt_to_hook: !v })}
                  onBrowse={() => browse('srt_dir')}         onClear={() => setConfig({ srt_dir: '', enable_srt: false })} />
                {config.enable_srt && (
                  <div className="space-y-1 rounded-[5px] border border-accent/20 bg-accent/[0.035] px-2.5 py-1">
                    <div className="flex h-7 items-center gap-2">
                      <span className="w-[88px] shrink-0 text-[10px] text-foreground/80">字幕位置<FeatureHelp topic="subtitle" title="字幕样式" /></span>
                      <div className="flex shrink-0 items-center gap-1">
                        {SUBTITLE_POSITION_PRESETS.map((preset) => {
                          const active = Math.abs(subtitleYPercent - preset.value) < 0.05
                          return (
                            <button
                              key={preset.label}
                              type="button"
                              onClick={() => setConfig({ subtitle_y_percent: preset.value })}
                              className={`h-6 rounded-[4px] border px-2 text-[9px] font-semibold transition-colors ${
                                active
                                  ? 'border-accent bg-accent text-background'
                                  : 'border-border/[0.08] bg-foreground/[0.015] text-muted-foreground hover:border-accent/50 hover:text-accent'
                              }`}
                            >
                              {preset.label}
                            </button>
                          )
                        })}
                      </div>
                      <span className="shrink-0 text-[9px] text-muted-foreground">上</span>
                      <Slider
                        ariaLabel="字幕垂直位置"
                        dragPreview={<SubtitlePreview resolution={config.resolution} yPercent={subtitleYPercent} fontSizePercent={subtitleFontSizePercent} />}
                        value={[subtitleYPercent]}
                        min={8}
                        max={92}
                        step={0.1}
                        onValueChange={([value]) => setConfig({ subtitle_y_percent: value })}
                        className="min-w-[100px] flex-1"
                      />
                      <span className="shrink-0 text-[9px] text-muted-foreground">下</span>
                      <span className="w-9 shrink-0 text-right font-mono text-[10px] text-accent">{subtitleYPercent.toFixed(1)}%</span>
                    </div>
                    <div className="flex h-7 items-center gap-2">
                      <span className="w-[72px] shrink-0 text-[10px] text-foreground/80">字体 / 字号</span>
                      <span title="内置 Adobe Source Han Sans SC Regular" className="h-6 shrink-0 rounded-[4px] border border-accent/30 bg-accent/[0.06] px-2 text-[9px] leading-6 text-accent">
                        思源黑体
                      </span>
                      <div className="flex shrink-0 items-center gap-1">
                        {SUBTITLE_FONT_SIZE_PRESETS.map((preset) => {
                          const active = Math.abs(subtitleFontSizePercent - preset.value) < 0.05
                          return (
                            <button
                              key={preset.label}
                              type="button"
                              onClick={() => setConfig({ subtitle_font_size_percent: preset.value })}
                              className={`h-6 rounded-[4px] border px-2 text-[9px] font-semibold transition-colors ${
                                active
                                  ? 'border-accent bg-accent text-background'
                                  : 'border-border/[0.08] bg-foreground/[0.015] text-muted-foreground hover:border-accent/50 hover:text-accent'
                              }`}
                            >
                              {preset.label}
                            </button>
                          )
                        })}
                      </div>
                      <Slider
                        ariaLabel="字幕字号"
                        dragPreview={<SubtitlePreview resolution={config.resolution} yPercent={subtitleYPercent} fontSizePercent={subtitleFontSizePercent} />}
                        value={[subtitleFontSizePercent]}
                        min={3}
                        max={9}
                        step={0.01}
                        onValueChange={([value]) => setConfig({ subtitle_font_size_percent: value })}
                        className="min-w-[90px] flex-1"
                      />
                      <span className="w-9 shrink-0 text-right font-mono text-[10px] text-accent">{subtitleFontSizePercent.toFixed(1)}%</span>
                    </div>
                  </div>
                )}
                <AssetCard kind="watermark" label="水印"      value={config.watermark_path || ''} pickAction="选择"
                  onChange={(v) => setConfig({ watermark_path: v })}
                  onOpen={() => openConfiguredPath(config.watermark_path || '', true)}
                  bodyOnly={!config.apply_watermark_to_hook} onBodyOnlyChange={(v) => setConfig({ apply_watermark_to_hook: !v })}
                  onBrowse={() => browseFile('watermark_path')} onClear={() => setConfig({ watermark_path: '' })} />
                <AssetCard kind="output"    label="输出目录"  value={config.base_out_dir} required
                  onChange={(v) => setConfig({ base_out_dir: v })}
                  onOpen={() => openConfiguredPath(config.base_out_dir)}
                  onBrowse={() => browse('base_out_dir')}    onClear={() => setConfig({ base_out_dir: '' })} />
              </div>
            </Group>

            {/* Parameters · overlap · volume · original action rail */}
            <div className="vm-card grid grid-cols-[minmax(0,1fr)_112px] gap-3 rounded-[16px] p-3">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 min-w-0">
                <Group title={config.selection_mode === 'speech_logic' ? '口播编排' : <span>参数<FeatureHelp topic="duration" title="时长模式" /></span>}>
                  <div className="space-y-1">
                    {config.selection_mode === 'speech_logic' ? <>
                      <p className="text-[10px] leading-4 text-accent">按完整话段剪辑；Hook 连成一整段，Body 顺接同一产品的话术。</p>
                      <ParamRow label="话段数" value={config.total_clips} onChange={(v) => setParam('total_clips', v)} />
                      <p className="text-[9px] leading-4 text-muted-foreground">每条 2–6 段，包含 Hook 的组成片段；完整 Hook 加一段连贯 Body 也可成片。实际时长由转录与方案决定。</p>
                    </> : <>
                    <div className="flex gap-1" role="group" aria-label="时长模式">
                      {(['clips', 'bgm'] as const).map(mode => <button key={mode} type="button"
                        aria-pressed={config.duration_mode === mode}
                        disabled={config.selection_mode === 'speech_logic' && mode === 'bgm'}
                        onClick={() => {
                          if (mode === 'bgm' && !config.bgm_dir.trim()) {
                            addToast('请先选择 BGM，再开启按 BGM 时长', 'warning')
                            document.querySelector<HTMLInputElement>('input[aria-label="BGM 路径"]')?.focus()
                            return
                          }
                          setConfig({ duration_mode: mode })
                        }}
                        className={`rounded border border-border/20 px-2 py-1 text-[10px] disabled:opacity-40 ${config.duration_mode === mode ? 'bg-accent text-background' : 'text-muted-foreground'}`}>
                        {mode === 'clips' ? '按片段数量' : '按 BGM 时长'}</button>)}
                    </div>
                    {config.duration_mode === 'bgm' && <p className="text-[10px] leading-4 text-accent">{config.apply_bgm_to_hook ? '全片按完整 BGM 时长；保留完整 Hook' : '总时长 = Hook + 完整 BGM'} · Body 自动裁尾 / 补齐</p>}
                    {config.duration_mode === 'bgm' && <p className="text-[10px] text-accent">预计成片 {durationLabel}</p>}
                    <div className="flex items-center gap-1">
                      <div className="min-w-0 flex-1"><RangeParamRow label="首段" min={config.t_hook_min} max={config.t_hook_max} disabled={config.hook_full_duration}
                        onMinChange={value => setParam('t_hook_min', value)} onMaxChange={value => setParam('t_hook_max', value)} /></div>
                      <label className="flex shrink-0 cursor-pointer items-center gap-1 text-[9px] text-foreground/85" title="完整使用每个 Hook；随机轮换，用完一轮再重复">
                        <Checkbox checked={config.hook_full_duration} onCheckedChange={value => setConfig({ hook_full_duration: value === true })} />按原素材时长
                      </label>
                    </div>
                    {config.hook_full_duration && config.body_mode === 'normal' && <p className="text-[9px] text-accent">预计成片 {durationLabel}</p>}
                    {config.body_mode === 'normal' ? <>
                      <div className="flex items-center gap-1">
                        <div className="min-w-0 flex-1"><RangeParamRow label="后段" disabled={config.body_full_duration} min={config.t_body_min} max={config.t_body_max}
                          onMinChange={value => setParam('t_body_min', value)} onMaxChange={value => setParam('t_body_max', value)} /></div>
                        <label className="flex shrink-0 cursor-pointer items-center gap-1 text-[9px] text-foreground/85">
                          <Checkbox aria-label="普通 Body 按原素材时长" checked={!!config.body_full_duration}
                            onCheckedChange={value => setConfig({ body_full_duration: value === true })} />按原素材时长
                        </label>
                      </div>
                      {config.body_full_duration && config.duration_mode !== 'bgm' && <p className="text-[9px] text-accent">{durationLabel}</p>}
                      {config.duration_mode === 'bgm' ? <ReadonlyParamRow label="片段" value="自动计算" /> : <ParamRow label="片段" value={config.total_clips} onChange={(v) => setParam('total_clips', v)} />}
                      <p className="text-[9px] leading-4 text-muted-foreground">每段在最短–最长秒数间取值；两端填相同秒数即固定时长。</p>
                    </> : <>
                      <ReadonlyParamRow label="总片段" value={config.duration_mode === 'bgm' ? '自动计算（按组循环）' : groupedClipCount} suffix="含 Hook" />
                      <ReadonlyParamRow label="总时长" value={durationLabel} />
                    </>}
                    </>}
                    <ParamRow label="数量" value={config.target_count} onChange={(v) => setParam('target_count', v)} />
                    <ParamRow label="并发" value={config.concurrent_tasks ?? 3} onChange={(v) => setParam('concurrent_tasks', v)} />
                  </div>
                </Group>

                <Group title={config.selection_mode === 'speech_logic' ? '原声 / 配乐音量' : <span className="inline-flex items-center">重叠率 / 音量 <OverlapRateHelp /></span>}>
                  <div className="space-y-1">
                    {config.selection_mode !== 'speech_logic' && <>
                    <ParamRow label="Hook" value={config.hook_full_duration ? 1 : config.hook_r} disabled={config.hook_full_duration} onChange={(v) => setParam('hook_r', v)} />
                    <ParamRow label="Body" disabled={allFullBody} value={config.body_r} onChange={(v) => setParam('body_r', v)} />
                    <ParamRow label="BGM-R" disabled={!config.bgm_dir.trim() || config.duration_mode === 'bgm'} value={config.bgm_r} onChange={(v) => setParam('bgm_r', v)} />
                    </>}
                    <ParamRow label="Hook声" value={config.vol_hook_orig} suffix="%" onChange={(v) => setParam('vol_hook_orig', v)} />
                    <ParamRow label="Body声" value={config.vol_orig} suffix="%" onChange={(v) => setParam('vol_orig', v)} />
                    <ParamRow label="BGM" disabled={!config.bgm_dir.trim()} value={config.vol_bgm} suffix="%" onChange={(v) => setParam('vol_bgm', v)} />
                  </div>
                </Group>
              </div>

              <div className="flex flex-col items-stretch justify-center gap-1.5 border-l border-border/[0.14] pl-3">
                <span className="text-center text-[10px] text-muted-foreground">操作<FeatureHelp topic="actions" title="操作按钮" /></span>
                <div className="space-y-1.5">
                  <button type="button" onClick={preFlight} disabled={preflightRunning} className="vm-action-secondary h-9 w-full px-2 text-[12px] font-semibold disabled:cursor-wait disabled:opacity-70">
                    {preflightRunning ? `预检中 ${preflightProgress?.percent ?? 0}%` : '预检产能'}
                  </button>
                  {preflightProgress && (
                    <div role="progressbar" aria-label="预检产能进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={preflightProgress.percent} className="min-w-0" title={preflightProgress.message}>
                      <div className="mb-0.5 flex items-center justify-between gap-1 text-[9px] text-muted-foreground">
                        <span className="min-w-0 truncate">{preflightProgress.message}</span>
                        <span className="shrink-0 font-mono tabular-nums">{preflightProgress.percent}%</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-foreground/[0.10]">
                        <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${preflightProgress.percent}%` }} />
                      </div>
                    </div>
                  )}
                </div>
                <button type="button" onClick={startRender} disabled={isRunning} className="vm-action-primary h-10 bg-accent px-2 text-[12px] font-bold text-background hover:bg-accent-hover disabled:opacity-50">
                  {isRunning ? '启动中…' : '启动渲染'}
                </button>
                <button type="button" onClick={clearHistory} className="vm-action-secondary h-8 px-2 text-[11px]">
                  清除记录
                </button>
                <button type="button" onClick={stopRunning} disabled={running === 0} className="h-8 rounded-[9px] border border-hot/30 bg-hot/[0.12] px-2 text-[11px] font-semibold text-hot hover:bg-hot/20 disabled:opacity-45">
                  停止
                </button>
                <button type="button" onClick={benchmark} disabled={benchmarkRunning} className="mt-0.5 h-7 rounded-[9px] border border-border/[0.16] bg-background/20 px-2 text-[10px] text-muted-foreground hover:border-accent/60 hover:text-accent disabled:cursor-wait disabled:border-accent/50 disabled:text-accent">
                  {benchmarkRunning ? `压测中 ${benchmarkProgress}%` : '智能压测'}
                </button>
              </div>
            </div>

            {/* Output */}
            <Group title="输出" className="flex min-h-0 flex-1 flex-col">
              <div className="vm-card grid flex-1 grid-cols-2 items-center gap-x-3 gap-y-2 rounded-[16px] p-3">
                <div className="flex items-center gap-2 min-w-0">
                  <div className="flex-1 min-w-0">
                    <ParamRow label="分辨率" value={config.resolution} placeholder="1080*1920" onChange={(v) => setConfig({ resolution: v })} />
                  </div>
                  <span className="w-12 shrink-0 font-mono text-[11px] text-accent">{getResolutionTag(config.resolution)}</span>
                  <div className="flex shrink-0 items-center gap-1">
                    {RESOLUTION_PRESETS.map((preset) => (
                      <button
                        key={preset.value}
                        type="button"
                        onClick={() => setConfig({ resolution: preset.value })}
                        className={`h-7 rounded-[4px] border px-2 text-[10px] font-semibold transition-colors ${
                          config.resolution === preset.value
                            ? 'border-accent bg-accent text-background'
                            : 'border-border/[0.10] bg-foreground/[0.02] text-muted-foreground hover:border-accent/60 hover:text-accent'
                        }`}
                        title={`9:16 ${preset.value}`}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </div>
                <ParamRow label="码率" value={config.bitrate} placeholder="8000k" onChange={(v) => setConfig({ bitrate: v })} />
                <ParamRow label="帧率" value={String(config.fps)} placeholder="29.97 / 30000/1001" onChange={(v) => setConfig({ fps: v as any })} />
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pl-2">
                  <label className="flex items-center gap-1.5 cursor-pointer">
                    <Checkbox checked={config.enable_gpu} onCheckedChange={(v) => setConfig({ enable_gpu: v as boolean })} />
                    <span className="text-[11px] text-foreground/85">GPU</span>
                  </label>
                  <FeatureHelp topic="gpu" title="GPU 加速" />
                  <label className="flex items-center gap-1.5 cursor-pointer" title="从成品随机抽帧并缩放裁切，可替换首帧或插入一帧；开启后会增加编码耗时。">
                    <Checkbox checked={config.enable_random_cover} onCheckedChange={(v) => setConfig({ enable_random_cover: v as boolean })} />
                    <span className="text-[11px] text-foreground/85">随机首帧</span>
                    <span className="text-[9px] text-hot">建议打开</span>
                  </label>
                  <FeatureHelp topic="cover" title="随机首帧" />
                  {config.enable_random_cover && (
                    <div className="flex items-center rounded-[4px] border border-border/[0.08] p-0.5">
                      {([['replace', '替换首帧'], ['insert', '插入一帧']] as const).map(([mode, label]) => (
                        <button key={mode} type="button" onClick={() => setConfig({ random_cover_mode: mode })}
                          className={`h-5 rounded-[3px] px-1.5 text-[9px] font-semibold ${config.random_cover_mode === mode ? 'bg-accent text-background' : 'text-muted-foreground hover:text-accent'}`}>
                          {label}
                        </button>
                      ))}
                    </div>
                  )}
                  <span className="text-[9px] text-muted-foreground">随机抽帧裁切；插入会延长 1 帧，音频同步后移</span>
                </div>
              </div>
            </Group>

          </div>

          {/* ─── RIGHT: telemetry panel ─── */}
          <div className="vm-card flex h-[360px] min-w-0 flex-col overflow-hidden rounded-[16px] xl:h-auto xl:min-h-0">
            {/* Tab strip */}
            <div className="vm-monitor-tabs flex shrink-0 items-center border-b border-border/[0.14] bg-background-elev/20 px-3">
              {([
                { k: 'log',    label: '日志', count: logs.length },
                ...(config.selection_mode === 'speech_logic' ? [{ k: 'speech' as const, label: '口播预览', count: visibleSpeechPreviews.length }] : []),
                { k: 'tasks',  label: '任务', count: tasks.length },
                { k: 'output', label: '产出', count: allOutputItems.length },
                { k: 'variant', label: '渲染设置', count: (config.enable_variants || (config.selection_mode === 'speech_logic' && config.no_fallback_mix)) ? 'ON' : 'OFF' },
              ] as const).map(t => (
                <button
                  key={t.k}
                  onClick={() => setRightTab(t.k as any)}
                  className={`relative px-3 py-1.5 text-[11px] font-medium transition-colors ${
                    rightTab === t.k ? 'text-accent' : 'text-muted-foreground hover:text-foreground/80'
                  }`}
                >
                  {t.label}
                  <span className="ml-1.5 font-mono opacity-70 tabular-nums">{t.count}</span>
                  {rightTab === t.k && <span className="absolute left-3 right-3 -bottom-px h-px bg-accent" />}
                </button>
              ))}
              {rightTab === 'log' && logs.length > 0 && (
                <button
                  onClick={clearLogs}
                  className="ml-auto pr-2 text-[10px] text-muted-foreground hover:text-foreground transition-colors font-mono"
                >
                  清空
                </button>
              )}
            </div>

            {/* Tab panel */}
            <div className="flex-1 min-h-0 relative">
              {/* LOG */}
              {rightTab === 'log' && (
                <div ref={logRef} aria-label="运行日志" onScroll={event => {
                  const panel = event.currentTarget
                  logScrollTopRef.current = panel.scrollTop
                  followLogsRef.current = panel.scrollHeight - panel.clientHeight - panel.scrollTop <= 8
                  }} className="vm-log-panel absolute inset-0 overflow-auto px-4 py-3 font-mono text-[11.5px] leading-[1.65]">
                  {logs.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无日志
                    </div>
                  ) : (
                    logs.map((l, i) => {
                      const isError = l.includes('错误') || l.toLowerCase().includes('error')
                      const isStart = l.startsWith('▸') || l.startsWith('>>>')
                      const isDone = l.startsWith('✅') || l.includes('完成')
                      const color =
                        isError ? 'text-hot' :
                        isStart ? 'text-accent' :
                        isDone ? 'text-ok' :
                        'text-foreground/75'
                      return (
                        <div key={i} className="flex gap-2.5">
                          <span className="text-muted/60 tabular-nums shrink-0 w-7 text-right select-none">
                            {String(i + 1).padStart(3, '0')}
                          </span>
                          <span className={`${color} whitespace-pre-wrap break-all`}>{l}</span>
                        </div>
                      )
                    })
                  )}
                </div>
              )}

              {/* SPOKEN SCRIPT PREVIEW */}
              {rightTab === 'speech' && config.selection_mode === 'speech_logic' && (
                <div role="region" aria-label="口播预览" className="vm-log-panel absolute inset-0 space-y-3 overflow-auto px-3 py-3">
                  {visibleSpeechPreviews.length ? <>
                    <p className="text-[10px] leading-4 text-muted-foreground">以下为预检的首个编排方案。{config.no_fallback_mix ? '风险素材可参与，但不按状态分级排序；已识别的不同产品不混用。' : '保底时同产品优先，再按可用、需复核、未采用依次选素材。'}请核对原声、Hook 是否完整，以及 Body 是否自然承接；风险方案发布前需人工复核。</p>
                    {visibleSpeechPreviews.map(item => item.speech_logic_preview && (
                      <SpeechPreviewCard key={item.name} item={item} />
                    ))}
                  </> : (
                    <div className="flex h-full items-center justify-center text-center text-[11px] leading-5 text-muted-foreground/70">
                      请点击“预检产能”查看转录与编排；修改素材或设置后需重新预检。
                    </div>
                  )}
                </div>
              )}

              {/* TASKS */}
              {rightTab === 'tasks' && (
                <div className="vm-log-panel absolute inset-0 overflow-auto px-4 py-2">
                  {tasks.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无任务
                    </div>
                  ) : (
                    tasks.slice().reverse().map(t => <TaskRow key={t.task_id} task={t} />)
                  )}
                </div>
              )}

              {/* OUTPUT */}
              {rightTab === 'output' && (
                <div className="vm-log-panel absolute inset-0 overflow-auto px-4 py-2">
                  {allOutputItems.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-muted-foreground/60 text-[12px]">
                      暂无产出
                    </div>
                  ) : (
                    allOutputItems.map(({ file: f, elapsed }, i) => (
                      <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border/[0.04] last:border-0">
                        <span className="text-[10px] text-muted-foreground font-mono tabular-nums w-6">
                          {String(i + 1).padStart(2, '0')}
                        </span>
                        <span className="flex-1 text-[11px] text-foreground/85 font-mono truncate">
                          {f.split('/').pop()}
                        </span>
                        {elapsed !== undefined && (
                          <span className="w-14 text-right text-[10px] font-mono text-accent">
                            {elapsed}s
                          </span>
                        )}
                        <button
                          onClick={() => window.electronAPI?.openPath(f)}
                          className="text-[10px] text-muted-foreground hover:text-accent transition-colors px-1.5"
                        >
                          打开
                        </button>
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* RENDER SETTINGS */}
              {rightTab === 'variant' && (
                <div className="vm-log-panel absolute inset-0 overflow-auto px-5 py-5">
                  <div className="border-b border-border/[0.10] pb-5">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="text-[13px] font-semibold text-foreground">不做保底混剪</div>
                        <p className="mt-1 text-[10px] leading-5 text-muted-foreground">仅适用于口播逻辑。开启后，语义方案不足时仍可使用风险素材，但不按“可用→需复核→未采用”分级排序；已识别的不同产品不混用。</p>
                      </div>
                      <label className={`mt-0.5 flex shrink-0 items-center gap-2 ${config.selection_mode === 'speech_logic' ? 'cursor-pointer' : 'cursor-not-allowed opacity-40'}`}>
                        <span className={`text-[11px] ${config.selection_mode === 'speech_logic' && config.no_fallback_mix ? 'text-accent' : 'text-muted-foreground'}`}>
                          {config.selection_mode === 'speech_logic' && config.no_fallback_mix ? '已开启' : '已关闭'}
                        </span>
                        <Checkbox
                          aria-label="不做保底混剪"
                          checked={Boolean(config.no_fallback_mix)}
                          disabled={config.selection_mode !== 'speech_logic'}
                          onCheckedChange={(value) => setConfig({ no_fallback_mix: value === true })}
                        />
                      </label>
                    </div>
                    <p className="mt-2 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-2 text-[10px] leading-5 text-foreground/75">
                      关闭时保持原有保底策略：优先同产品，再依次选可用、需复核、未采用素材。两种模式下的风险方案都需要人工复核。
                    </p>
                  </div>

                  <div className="pt-5">
                  <div className="flex items-center justify-between border-b border-border/[0.06] pb-4">
                    <div>
                      <div className="text-[13px] font-semibold text-foreground">成品去重变换<FeatureHelp topic="variants" title="成品变换" /></div>
                      <div className="mt-1 font-mono text-[10px] text-muted-foreground">OUTPUT TRANSFORMER V1</div>
                    </div>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <span className={`text-[11px] ${config.enable_variants ? 'text-accent' : 'text-muted-foreground'}`}>
                        {config.enable_variants ? '已开启' : '已关闭'}
                      </span>
                      <Checkbox
                        checked={config.enable_variants}
                        onCheckedChange={(v) => setConfig({
                          enable_variants: v as boolean,
                          variant_hook: true,
                          variant_body: true,
                          variant_mirror: false,
                          variant_frame_mix: true,
                        })}
                      />
                    </label>
                  </div>

                  <div className={`space-y-5 pt-5 transition-opacity ${config.enable_variants ? 'opacity-100' : 'pointer-events-none opacity-40'}`}>
                    <div className="rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-3 text-[11px] leading-5 text-foreground/80">
                      混剪完成后自动处理最终成品。关闭时不增加任何处理步骤，也不会修改 Hook、Body 等源素材。
                    </div>

                    <div>
                      <div className="mb-2 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">强度</div>
                      <div className="grid grid-cols-3 gap-1 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] p-1">
                        {([
                          { value: 'mild', label: VARIANT_STRENGTH_INFO.mild.label },
                          { value: 'balanced', label: VARIANT_STRENGTH_INFO.balanced.label },
                          { value: 'strong', label: VARIANT_STRENGTH_INFO.strong.label },
                        ] as const).map((item) => (
                          <button
                            key={item.value}
                            type="button"
                            onClick={() => {
                              setConfig({ variant_strength: item.value })
                              addToast(`${item.label}：${VARIANT_STRENGTH_INFO[item.value].description}`, 'info')
                            }}
                            className={`h-8 rounded-[3px] text-[11px] font-semibold transition-colors ${
                              config.variant_strength === item.value
                                ? 'bg-accent text-background'
                                : 'text-muted-foreground hover:bg-foreground/[0.04] hover:text-foreground'
                            }`}
                          >
                            {item.label}
                          </button>
                        ))}
                      </div>
                      <div className="mt-2 rounded-[4px] border border-accent/20 bg-accent/[0.05] px-3 py-2 text-[11px] leading-5 text-foreground/80">
                        <span className="mr-1 font-semibold text-accent">
                          {VARIANT_STRENGTH_INFO[config.variant_strength].label}：
                        </span>
                        {VARIANT_STRENGTH_INFO[config.variant_strength].description}
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-x-5 gap-y-2 border-t border-border/[0.06] pt-4 font-mono text-[10px]">
                      <div className="flex justify-between"><span className="text-muted-foreground">处理对象</span><span className="text-accent">最终成品</span></div>
                      <div className="flex justify-between"><span className="text-muted-foreground">源素材</span><span className="text-ok">不修改</span></div>
                      <div className="flex justify-between"><span className="text-muted-foreground">输出规格</span><span className="text-ok">保持</span></div>
                      <div className="flex justify-between"><span className="text-muted-foreground">失败策略</span><span className="text-ok">保留原成品</span></div>
                    </div>
                  </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
          </div>
        </div>
      </div>

      {completionNotice && (
        <div className="fixed left-1/2 top-16 z-[1200] -translate-x-1/2 rounded-[6px] border border-ok/50 bg-background-elev px-7 py-4 text-center shadow-[0_20px_60px_-20px_rgba(155,214,107,0.8)]">
          <div className="text-[16px] font-semibold text-ok">任务完成</div>
          <div className="mt-1 max-w-[520px] truncate text-[12px] text-foreground/80">{completionNotice}</div>
        </div>
      )}

    </div>
  )
}
