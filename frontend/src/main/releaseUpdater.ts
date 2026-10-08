import { app, BrowserWindow, dialog, net, shell } from 'electron'
import { spawn } from 'child_process'
import path from 'path'
import {
  AvailableUpdate, UpdatePlatform, channelManifestUrl,
  compareStableZeroVersions, parseStableZeroVersion, validateUpdateManifest,
} from './updateChannel'
import { downloadVerifiedAsset, UpdateDownloadProgress } from './updateDownloader'

export type UpdateCheckResult =
  | { status: 'available'; currentVersion: string; update: AvailableUpdate }
  | { status: 'current' | 'unpublished' | 'unsupported'; currentVersion: string; message: string }

export type { UpdateDownloadProgress } from './updateDownloader'

function supportedPlatform(): UpdatePlatform | null {
  return process.platform === 'win32' || process.platform === 'darwin' ? process.platform : null
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
  return { status: 'available', currentVersion, update }
}

let installing = false

export async function downloadAndInstallUpdate(
  owner: BrowserWindow,
  onProgress: (progress: UpdateDownloadProgress) => void,
  closeCurrentApp: () => Promise<void>,
): Promise<'canceled' | 'installer-launched' | 'dmg-opened'> {
  if (installing) throw new Error('更新已经在下载中，请稍候。')
  installing = true
  try {
    if (!app.isPackaged) throw new Error('开发模式不能安装更新，请使用正式安装版。')
    const result = await checkForUpdate()
    if (result.status !== 'available') throw new Error(result.message || '没有可安装的新版本。')
    const { update } = result
    const choice = await dialog.showMessageBox(owner, {
    type: 'question',
    buttons: ['取消', '下载并安装'],
    defaultId: 0,
    cancelId: 0,
    title: '更新巨能跑pro版',
    message: `将更新到 v${update.version}`,
    detail: process.platform === 'win32'
      ? '会下载并校验官方安装包，然后关闭软件、停止当前任务并启动安装向导。请保存其他未完成工作。'
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
