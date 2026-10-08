const getBaseUrl = async (): Promise<string> => {
  if (window.electronAPI) {
    const port = await window.electronAPI.getBackendPort()
    return `http://127.0.0.1:${port}/api`
  }
  return 'http://127.0.0.1:8765/api'
}

function formatApiError(detail: unknown, fallback: string): string {
  const fieldLabels: Record<string, string> = {
    hook_r: 'Hook 重叠率', body_r: 'Body 重叠率', bgm_r: 'BGM 重叠率',
    t_hook: '首段时长', t_body: '后段时长',
    t_hook_min: '首段最短秒数', t_hook_max: '首段最长秒数',
    t_body_min: '后段最短秒数', t_body_max: '后段最长秒数', total_clips: '片段数',
    target_count: '生成数量', concurrent_tasks: '并发数', vol_hook_orig: 'Hook 原声音量',
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const error = item as { loc?: unknown[]; msg?: string }
        const field = error.loc?.at(-1)
        const label = field ? fieldLabels[String(field)] || String(field) : ''
        return label && error.msg ? `${label}：${error.msg}` : error.msg
      })
      .filter((message): message is string => Boolean(message))
    if (messages.length) return messages.join('；')
  }
  return fallback
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(formatApiError(err.detail, `请求失败（HTTP ${res.status}）`))
  }
  return res.json()
}

export interface BodyGroup {
  full_duration?: boolean
  enabled: boolean
  folder: string
  clip_count: string | number
  clip_duration: string | number
}

export interface VideoConfig {
  task_name: string
  hook_dir: string
  body_dirs: string[]
  body_mode: 'normal' | 'grouped'
  body_groups: BodyGroup[]
  selection_mode: 'random' | 'speech_logic'
  semantic_sku?: string
  semantic_topic?: string
  bgm_dir: string
  duration_mode: 'clips' | 'bgm'
  voice_dir?: string
  srt_dir?: string
  watermark_path?: string
  base_out_dir: string
  t_hook: string | number
  t_hook_min: string | number
  t_hook_max: string | number
  hook_full_duration: boolean
  t_body: string | number
  t_body_min: string | number
  t_body_max: string | number
  body_full_duration?: boolean
  total_clips: string | number
  target_count: string | number
  hook_r: string | number
  body_r: string | number
  bgm_r: string | number
  resolution: string
  fps: string | number
  bitrate: string
  vol_orig: string | number
  vol_hook_orig: string | number
  vol_bgm: string | number
  vol_voice: string | number
  apply_bgm_to_hook: boolean
  apply_voice_to_hook: boolean
  apply_srt_to_hook: boolean
  apply_watermark_to_hook: boolean
  enable_srt: boolean
  subtitle_y_percent: number
  subtitle_font_size_percent: number
  enable_gpu: boolean
  concurrent_tasks?: string | number
  enable_variants: boolean
  variant_strength: 'mild' | 'balanced' | 'strong'
  variant_hook: boolean
  variant_body: boolean
  variant_mirror: boolean
  variant_frame_mix: boolean
  variant_seed?: number | null
  enable_random_cover: boolean
  random_cover_mode: 'replace' | 'insert'
}

const NUMERIC_CONFIG_FIELDS = [
  't_hook', 't_hook_min', 't_hook_max', 't_body', 't_body_min', 't_body_max',
  'total_clips', 'target_count', 'hook_r', 'body_r', 'bgm_r',
  'fps', 'vol_orig', 'vol_hook_orig', 'vol_bgm', 'vol_voice', 'subtitle_y_percent',
  'subtitle_font_size_percent', 'concurrent_tasks', 'variant_seed',
] as const

function numberIfValid(value: unknown) {
  if (typeof value === 'number' || value === null) return value
  if (typeof value !== 'string' || !value.trim()) return value
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : value
}

export function normalizeConfigForRequest(config: VideoConfig): VideoConfig {
  const normalized = { ...config } as VideoConfig
  NUMERIC_CONFIG_FIELDS.forEach((key) => {
    ;(normalized as any)[key] = numberIfValid(config[key])
  })
  // Keep the former scalar fields for older automation clients and use the
  // minimum as their fallback. The backend reads the explicit range fields.
  normalized.t_hook = normalized.t_hook_min
  normalized.t_body = normalized.t_body_min
  normalized.body_mode = config.body_mode === 'grouped' ? 'grouped' : 'normal'
  normalized.selection_mode = config.selection_mode === 'speech_logic' ? 'speech_logic' : 'random'
  normalized.semantic_sku = String(config.semantic_sku || '').trim()
  // Speech-logic preflight groups by product only; a previously saved theme
  // must not silently narrow or reject a run after that control was removed.
  normalized.semantic_topic = normalized.selection_mode === 'speech_logic'
    ? ''
    : String(config.semantic_topic || '').trim()
  if (normalized.selection_mode === 'speech_logic') {
    // These controls are hidden while complete spoken units set the edit points.
    normalized.t_hook = 3
    normalized.t_body = 3
    normalized.t_hook_min = normalized.t_hook_max = 3
    normalized.t_body_min = normalized.t_body_max = 3
    normalized.hook_r = 0.5
    normalized.body_r = 0.5
    normalized.bgm_r = 0.3
    normalized.hook_full_duration = false
    normalized.body_full_duration = false
  }
  const normalizedGroups = (config.body_groups || []).slice(0, 4).map((group) => {
    const safeGroup = group || {} as BodyGroup
    const clipCount = Number(safeGroup.clip_count)
    const clipDuration = Number(safeGroup.clip_duration)
    return {
      enabled: Boolean(safeGroup.enabled),
      folder: String(safeGroup.folder || '').trim(),
      full_duration: Boolean(safeGroup.full_duration),
      // Disabled rows stay at their position but must still satisfy Pydantic.
      clip_count: Number.isInteger(clipCount) && clipCount >= 1 ? clipCount : 1,
      clip_duration: Number.isFinite(clipDuration) && clipDuration >= 0.5 ? clipDuration : 3,
    }
  })
  normalized.body_groups = normalized.body_mode === 'grouped' ? normalizedGroups : []
  if (normalized.body_mode === 'grouped') {
    const groupClipCount = normalizedGroups.filter((group) => group.enabled)
      .reduce((total, group) => total + group.clip_count, 0)
    // These normal-mode inputs are hidden in grouped mode, so never submit stale invalid values.
    normalized.t_body = 3
    normalized.t_body_min = normalized.t_body_max = 3
    normalized.total_clips = Math.max(2, 1 + groupClipCount)
  }
  normalized.enable_random_cover = Boolean(config.enable_random_cover)
  normalized.body_full_duration = normalized.selection_mode === 'speech_logic' ? false : Boolean(config.body_full_duration)
  if (normalized.body_full_duration) {
    normalized.t_body = 3
    normalized.t_body_min = normalized.t_body_max = 3
  }
  if (normalized.body_mode === 'grouped' ? normalizedGroups.filter(g => g.enabled).every(g => g.full_duration) : normalized.body_full_duration) normalized.body_r = 0
  normalized.hook_full_duration = normalized.selection_mode === 'speech_logic' ? false : Boolean(config.hook_full_duration)
  if (normalized.hook_full_duration) {
    normalized.t_hook = 3 // Hidden fixed-duration input must not invalidate this mode.
    normalized.t_hook_min = normalized.t_hook_max = 3
    normalized.hook_r = 1
  }
  normalized.random_cover_mode = config.random_cover_mode === 'insert' ? 'insert' : 'replace'
  return normalized
}

export interface TaskStatus {
  task_id: string
  task_name: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'stopped'
  progress: number
  current: number
  total: number
  message: string
  log_lines: string[]
  created_at: string
  updated_at?: string
  output_files: string[]
  output_elapsed?: Record<string, number>
  acceleration?: string
  acceleration_warning?: string
  effective_concurrency?: number
}

export interface AccountUser {
  id: number | string
  phone: string
  created_at: string
}

export interface AccountSession {
  token: string
  user: AccountUser
}

export interface SpeechLogicPreview {
  fallback?: boolean
  fallback_mode?: 'clip_level'
  product_id?: string
  sku_id: string
  topic_id: string
  pain_id?: string
  mechanism_id?: string
  capacity: number
  transcripts: Array<{
    source_file: string
    source_type: 'hook' | 'body' | 'both'
    text: string
    cues: Array<{ start_s: number; end_s: number; text: string }>
    status?: 'usable' | 'review' | 'blocked'
    reasons?: string[]
    product_id?: string
    sku_id?: string
    topic_id?: string
    source_unit_count?: number
    usable_unit_count?: number
  }>
  segments: Array<{
    role: 'hook' | 'body'
    source_file: string
    start_s: number
    end_s: number
    text: string
    product_id?: string
    sku_id: string
    topic_id: string
    pain_id?: string | null
    mechanism_id?: string | null
  }>
  transcript: string
  hook_duration_s: number
  hook_segment_count: number
  warnings: string[]
}

export interface PreflightReportItem {
  name: string
  ok: boolean
  capacity: number | string
  message: string
  speech_logic_preview?: SpeechLogicPreview
}

export interface PreflightResult {
  ok: boolean
  capacity: number | string
  report: PreflightReportItem[]
  error?: string
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  registerAccount: (phone: string, password: string) =>
    request<AccountSession>('/account/register', {
      method: 'POST', body: JSON.stringify({ phone, password }),
    }),

  loginAccount: (phone: string, password: string) =>
    request<AccountSession>('/account/login', {
      method: 'POST', body: JSON.stringify({ phone, password }),
    }),

  accountMe: (token: string) =>
    request<{ user: AccountUser }>('/account/me', {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    }),

  logoutAccount: (token: string) =>
    request<{ message: string }>('/account/logout', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    }),

  createTask: (config: VideoConfig) =>
    request<{ task_id: string; message: string }>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ config: normalizeConfigForRequest(config) }),
    }),

  listTasks: () => request<TaskStatus[]>('/tasks'),

  getTask: (taskId: string) => request<TaskStatus>(`/tasks/${taskId}`),

  stopTask: (taskId: string) =>
    request<{ message: string }>(`/tasks/${taskId}/stop`, { method: 'POST' }),

  getLogs: (taskId: string) => request<{ logs: string[] }>(`/tasks/${taskId}/logs`),

  streamLogs: async (taskId: string, onLog: (data: any) => void) => {
    const baseUrl = await getBaseUrl()
    const eventSource = new EventSource(`${baseUrl}/tasks/${taskId}/stream`)
    eventSource.onmessage = (e) => {
      if (e.data === '[DONE]') {
        eventSource.close()
        return
      }
      try {
        onLog(JSON.parse(e.data))
      } catch {
        onLog({ log: e.data })
      }
    }
    eventSource.onerror = () => eventSource.close()
    return () => eventSource.close()
  },

  scanDirectory: (dirPath: string, extensions: string[] = ['.mp4', '.mov']) =>
    request<{ files: string[]; count: number }>('/scan', {
      method: 'POST',
      body: JSON.stringify({ dir_path: dirPath, extensions }),
    }),

  probeFile: (filePath: string) =>
    request<any>(`/probe?file_path=${encodeURIComponent(filePath)}`, { method: 'POST' }),

  benchmark: (config: VideoConfig) =>
    request<any>('/benchmark', {
      method: 'POST',
      body: JSON.stringify(normalizeConfigForRequest(config)),
    }),

  preflight: (config: VideoConfig) =>
    request<PreflightResult>('/preflight', {
      method: 'POST',
      body: JSON.stringify(normalizeConfigForRequest(config)),
    }),

  clearHistory: () =>
    request<{ message: string }>('/history/clear', { method: 'POST' }),
}
