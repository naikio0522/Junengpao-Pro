import { contextBridge, ipcRenderer } from 'electron'

export interface ElectronAPI {
  openDirectory: (defaultPath?: string, multi?: boolean) => Promise<string | string[] | null>
  openFile: (filters?: { name: string; extensions: string[] }[], defaultPath?: string) => Promise<string | null>
  openVideoFiles: (defaultPath?: string, extensions?: string[]) => Promise<string[] | null>
  saveTextFile: (defaultName: string, content: string, filters?: { name: string; extensions: string[] }[]) => Promise<string | null>
  openPath: (filePath: string) => Promise<void>
  openExternalHttps: (url: string) => Promise<void>
  getBackendPort: () => Promise<number>
  getBackendToken: () => Promise<string>
  checkForUpdates: () => Promise<UpdateCheckResult>
  downloadAndInstallUpdate: () => Promise<'canceled' | 'installer-launched' | 'dmg-opened'>
  onUpdateDownloadProgress: (listener: (progress: UpdateDownloadProgress) => void) => () => void
}

export interface UpdateDownloadProgress {
  received: number
  total: number
  percent: number
}

export type UpdateCheckResult =
  | { status: 'available'; currentVersion: string; update: {
      version: string; notes: string; releasePage: string; size: number
    } }
  | { status: 'current' | 'unpublished' | 'unsupported'; currentVersion: string; message: string }

const api: ElectronAPI = {
  openDirectory: (defaultPath, multi = false) => ipcRenderer.invoke('dialog:openDirectory', defaultPath, multi),
  openFile: (filters, defaultPath) => ipcRenderer.invoke('dialog:openFile', filters, defaultPath),
  openVideoFiles: (defaultPath, extensions) => ipcRenderer.invoke('dialog:openVideoFiles', defaultPath, extensions),
  saveTextFile: (defaultName, content, filters) => ipcRenderer.invoke('dialog:saveText', defaultName, content, filters),
  openPath: (filePath) => ipcRenderer.invoke('shell:openPath', filePath),
  openExternalHttps: (url) => ipcRenderer.invoke('shell:openExternalHttps', url),
  getBackendPort: () => ipcRenderer.invoke('app:getBackendPort'),
  getBackendToken: () => ipcRenderer.invoke('app:getBackendToken'),
  checkForUpdates: () => ipcRenderer.invoke('app:checkForUpdates'),
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
