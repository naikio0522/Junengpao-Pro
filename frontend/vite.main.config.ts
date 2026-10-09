import { defineConfig } from 'vite'
import { builtinModules } from 'module'
import path from 'path'

// The public HTTPS endpoint may be supplied by the release build. No account
// database credentials or service keys are ever embedded in the desktop app.
const accountApiUrl = (process.env.JNP_DESKTOP_ACCOUNT_API_URL || '').trim()
if (accountApiUrl) {
  const endpoint = new URL(accountApiUrl)
  if (endpoint.protocol !== 'https:' || !endpoint.hostname || endpoint.username ||
      endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== '/') {
    throw new Error('JNP_DESKTOP_ACCOUNT_API_URL must be an HTTPS domain root')
  }
}

export default defineConfig({
  define: {
    __JNP_DESKTOP_ACCOUNT_API_URL__: JSON.stringify(accountApiUrl),
  },
  build: {
    lib: {
      entry: path.resolve(__dirname, 'src/main/main.ts'),
      formats: ['cjs'],
      fileName: () => 'main.js',
    },
    outDir: 'dist/main',
    emptyOutDir: true,
    rollupOptions: {
      // Mark Electron + all Node built-ins (and their `node:` variants) as
      // external so they are not bundled or replaced with browser shims.
      external: [
        'electron',
        ...builtinModules,
        ...builtinModules.map(m => `node:${m}`),
      ],
    },
    target: 'node18',
    minify: false,
  },
})
