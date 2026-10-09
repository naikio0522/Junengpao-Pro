import { app, BrowserWindow, dialog, net, shell } from 'electron'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import {
  AvailableUpdate, UpdatePlatform, channelManifestUrl,
  compareStableZeroVersions, parseStableZeroVersion, validateUpdateManifest,
} from './updateChannel'
import { downloadVerifiedAsset, UpdateDownloadProgress } from './updateDownloader'

export type UpdateCheckResult =
  | { status: 'available'; currentVersion: string; update: AvailableUpdate }
  | { status: 'required'; currentVersion: string; update: AvailableUpdate; usingCachedPolicy?: boolean }
  | { status: 'current' | 'unpublished' | 'unsupported'; currentVersion: string; message: string }

export type { UpdateDownloadProgress } from './updateDownloader'

function supportedPlatform(): UpdatePlatform | null {
  return process.platform === 'win32' || process.platform === 'darwin' ? process.platform : null
}

function requiredPolicyCachePath(): string {
  return path.join(app.getPath('userData'), 'required-update-0x.json')
}

function readRequiredPolicyCache(): Extract<UpdateCheckResult, { status: 'required' }> | null {
  const platform = supportedPlatform()
  if (!platform) return null
  try {
    const cached = JSON.parse(fs.readFileSync(requiredPolicyCachePath(), 'utf8')) as {
      schema?: number
      currentVersion?: string
      update?: Partial<AvailableUpdate>
    }
    const currentVersion = app.getVersion()
    if (cached.schema !== 1 || cached.currentVersion !== currentVersion ||
        !parseStableZeroVersion(currentVersion) || !cached.update) return null
    const update = validateUpdateManifest({
      channel: '0.x', version: cached.update.version,
      minimumSupportedVersion: cached.update.minimumSupportedVersion,
      notes: cached.update.notes,
      asset: { url: cached.update.url, sha256: cached.update.sha256, size: cached.update.size },
    }, platform)
    if (!update.minimumSupportedVersion ||
        compareStableZeroVersions(currentVersion, update.minimumSupportedVersion)! >= 0 ||
        compareStableZeroVersions(update.version, currentVersion)! <= 0) return null
    return { status: 'required', currentVersion, update, usingCachedPolicy: true }
  } catch {
    return null
  }
}

function saveRequiredPolicyCache(result: Extract<UpdateCheckResult, { status: 'required' }>): void {
  try {
    fs.writeFileSync(requiredPolicyCachePath(), JSON.stringify({
      schema: 1, currentVersion: result.currentVersion, update: result.update,
    }), { encoding: 'utf8', mode: 0o600 })
  } catch {
    // The current online decision still applies even if local persistence fails.
  }
}

function clearRequiredPolicyCache(): void {
  try { fs.unlinkSync(requiredPolicyCachePath()) } catch { /* No prior policy. */ }
}

export async function checkForUpdate(): Promise<UpdateCheckResult> {
  const currentVersion = app.getVersion()
  const platform = supportedPlatform()
  if (!platform || !parseStableZeroVersion(currentVersion)) {
    return { status: 'unsupported', currentVersion, message: '当前版本或系统不属于 0.x 更新通道。' }
  }
  const url = `${channelManifestUrl(platform)}?t=${Date.now()}`
  let response: Response
  try {
    response = await net.fetch(url, {
      headers: { 'User-Agent': `VideoMatrix/${currentVersion}`, Accept: 'application/json' },
      signal: AbortSignal.timeout(15000),
      cache: 'no-store',
    })
  } catch {
    throw new Error('检查更新失败：暂时无法连接 0.x 更新通道，请检查网络后重试。')
  }
  if (response.status === 404) {
    return { status: 'unpublished', currentVersion, message: '0.x 更新通道尚未发布；不会安装旧的 2.x 版本。' }
  }
  if (!response.ok) throw new Error(`检查更新失败：服务器返回 ${response.status}。`)
  const update = validateUpdateManifest(await response.json(), platform)
  const comparison = compareStableZeroVersions(update.version, currentVersion)
  if (comparison === null) {
    return { status: 'unsupported', currentVersion, message: '当前版本不属于 0.x 更新通道。' }
  }
  if (comparison <= 0) {
    return { status: 'current', currentVersion, message: `已是 0.x 通道的最新版本（v${currentVersion}）。` }
  }
  const minimum = update.minimumSupportedVersion
  return {
    status: minimum && compareStableZeroVersions(currentVersion, minimum)! < 0
      ? 'required' : 'available',
    currentVersion,
    update,
  }
}

/** Startup uses the last validated required policy only when the live channel fails. */
export async function checkForStartupUpdate(): Promise<UpdateCheckResult> {
  try {
    const result = await checkForUpdate()
    if (result.status === 'required') saveRequiredPolicyCache(result)
    else if (result.status === 'available' || result.status === 'current') clearRequiredPolicyCache()
    else if (result.status === 'unpublished') return readRequiredPolicyCache() || result
    return result
  } catch (error) {
    const cached = readRequiredPolicyCache()
    if (cached) return cached
    throw error
  }
}

let installing = false

export async function downloadAndInstallUpdate(
  owner: BrowserWindow,
  onProgress: (progress: UpdateDownloadProgress) => void,
  closeCurrentApp: () => Promise<void>,
  ensureNoActiveTasks: () => Promise<void>,
): Promise<'canceled' | 'installer-launched' | 'dmg-opened'> {
  if (installing) throw new Error('更新已经在下载中，请稍候。')
  installing = true
  try {
    if (!app.isPackaged) throw new Error('开发模式不能安装更新，请使用正式安装版。')
    const result = await checkForUpdate()
    if (result.status !== 'available' && result.status !== 'required') {
      throw new Error(result.message || '没有可安装的新版本。')
    }
    const { update } = result
    const choice = await dialog.showMessageBox(owner, {
    type: 'question',
    buttons: ['取消', '下载并安装'],
    defaultId: 0,
    cancelId: 0,
    title: '更新巨能跑pro版',
    message: `将更新到 v${update.version}`,
    detail: process.platform === 'win32'
      ? '会下载并校验官方安装包。安装前会再次检查任务；若仍有任务在处理，请等任务完成后重试。确认无任务后才关闭软件并启动安装向导。'
      : '会下载并校验官方 DMG，然后打开安装镜像；macOS 版本仍需按系统提示完成安装。',
    })
    if (choice.response !== 1) return 'canceled'
    const installer = await downloadVerifiedAsset(update, supportedPlatform()!,
      path.join(app.getPath('temp'), 'VideoMatrix-0x-updates'), onProgress,
      (url, options) => net.fetch(String(url), options))
    if (process.platform === 'darwin') {
      const openError = await shell.openPath(installer)
      if (openError) throw new Error(`无法打开已校验的安装镜像：${openError}`)
      return 'dmg-opened'
    }
    // Interactive NSIS preserves the user's installation-directory choice.
    // It is launched only after the user opted in and SHA-256 was verified.
    // Tasks may start during a long download. Check immediately before launching
    // the installer so an update cannot silently interrupt a render.
    await ensureNoActiveTasks()
    const child = spawn(installer, [], { detached: true, stdio: 'ignore', windowsHide: false })
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', () => resolve())
      child.once('error', reject)
    })
    child.unref()
    void closeCurrentApp()
    return 'installer-launched'
  } finally {
    installing = false
  }
}
