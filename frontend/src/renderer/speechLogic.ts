import type { VideoConfig } from './api/client'

function directoryKey(value: string): string {
  return value.trim().replace(/\\/g, '/').replace(/\/+$/, '').toLocaleLowerCase()
}

export function speechLogicConfigError(config: VideoConfig): string | null {
  if (config.duration_mode !== 'clips') return '口播逻辑模式请使用按片段数量'
  if (config.body_mode !== 'normal') return '口播逻辑模式请使用普通 Body'
  if (config.voice_dir?.trim() || config.enable_srt) return '口播逻辑模式请清空外部配音并关闭随机字幕'
  const bodyDirs = config.body_dirs.map(path => path.trim()).filter(Boolean)
  if (!bodyDirs.length) return '口播逻辑模式请分别选择 Hook 和 Body 素材目录'
  if (bodyDirs.some(path => directoryKey(path) === directoryKey(config.hook_dir))) {
    return '口播逻辑模式的 Hook 与 Body 目录不能相同'
  }
  const totalClips = Number(config.total_clips)
  if (!Number.isInteger(totalClips) || totalClips < 2 || totalClips > 6) {
    return '口播逻辑模式每条成片请选择 2–6 段完整话段'
  }
  const hookVolume = config.vol_hook_orig == null ? Number(config.vol_orig) : Number(config.vol_hook_orig)
  if (!(hookVolume > 0) || !(Number(config.vol_orig) > 0)) {
    return '口播逻辑模式需要保留 Hook 和 Body 原声，请将原声音量设为大于 0%'
  }
  return null
}
