import { contextBridge, ipcRenderer } from 'electron'

export interface ElectronAPI {
  openDirectory: (defaultPath?: string, multi?: boolean) => Promise<string | string[] | null>
  openFile: (filters?: { name: string; extensions: string[] }[], defaultPath?: string) => Promise<string | null>
  openVideoFiles: (defaultPath?: string, extensions?: string[]) => Promise<string[] | null>
  saveTextFile: (defaultName: string, content: string, filters?: { name: string; extensions: string[] }[]) => Promise<string | null>
  openPath: (filePath: string) => Promise<void>
  openExternalHttps: (url: string) => Promise<void>
  resolveDouyinPublicPlayer: (videoId: string) => Promise<Record<string, unknown>>
  getBackendPort: () => Promise<number>
  getBackendToken: () => Promise<string>
  isPackaged: () => Promise<boolean>
  checkForUpdates: () => Promise<UpdateCheckResult>
  checkForStartupUpdate: () => Promise<UpdateCheckResult>
  getActiveTaskCount: () => Promise<number>
  downloadAndInstallUpdate: () => Promise<'canceled' | 'installer-launched' | 'dmg-opened'>
  onUpdateDownloadProgress: (listener: (progress: UpdateDownloadProgress) => void) => () => void
}

export interface UpdateDownloadProgress {
  received: number
  total: number
  percent: number
}

export type UpdateCheckResult =
  | { status: 'available' | 'required'; currentVersion: string; usingCachedPolicy?: boolean; update: {
      version: string; minimumSupportedVersion?: string; notes: string; releasePage: string; size: number
    } }
  | { status: 'current' | 'unpublished' | 'unsupported'; currentVersion: string; message: string }

const api: ElectronAPI = {
  openDirectory: (defaultPath, multi = false) => ipcRenderer.invoke('dialog:openDirectory', defaultPath, multi),
  openFile: (filters, defaultPath) => ipcRenderer.invoke('dialog:openFile', filters, defaultPath),
  openVideoFiles: (defaultPath, extensions) => ipcRenderer.invoke('dialog:openVideoFiles', defaultPath, extensions),
  saveTextFile: (defaultName, content, filters) => ipcRenderer.invoke('dialog:saveText', defaultName, content, filters),
  openPath: (filePath) => ipcRenderer.invoke('shell:openPath', filePath),
  openExternalHttps: (url) => ipcRenderer.invoke('shell:openExternalHttps', url),
  resolveDouyinPublicPlayer: (videoId) => ipcRenderer.invoke('douyin:resolvePublicPlayer', videoId),
  getBackendPort: () => ipcRenderer.invoke('app:getBackendPort'),
  getBackendToken: () => ipcRenderer.invoke('app:getBackendToken'),
  isPackaged: () => ipcRenderer.invoke('app:isPackaged'),
  checkForUpdates: () => ipcRenderer.invoke('app:checkForUpdates'),
  checkForStartupUpdate: () => ipcRenderer.invoke('app:checkForStartupUpdate'),
  getActiveTaskCount: () => ipcRenderer.invoke('app:getActiveTaskCount'),
  downloadAndInstallUpdate: () => ipcRenderer.invoke('app:downloadAndInstallUpdate'),
  onUpdateDownloadProgress: listener => {
    const handler = (_event: Electron.IpcRendererEvent, progress: UpdateDownloadProgress) => listener(progress)
    ipcRenderer.on('app:updateDownloadProgress', handler)
    return () => ipcRenderer.removeListener('app:updateDownloadProgress', handler)
  },
}

contextBridge.exposeInMainWorld('electronAPI', api)

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}
