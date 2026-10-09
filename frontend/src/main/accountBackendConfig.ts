/** Account routing for the desktop backend. Release packages always use cloud mode. */
export function resolveAccountBackendConfig(
  isPackaged: boolean,
  env: NodeJS.ProcessEnv,
  embeddedUrl: string,
): { mode: string; apiUrl: string } {
  const runtimeUrl = env.JNP_ACCOUNT_API_URL?.trim() || ''
  const apiUrl = isPackaged ? embeddedUrl || runtimeUrl : runtimeUrl || embeddedUrl
  return {
    mode: isPackaged ? 'cloud' : (env.JNP_ACCOUNT_MODE || (apiUrl ? 'cloud' : 'local_test')),
    apiUrl,
  }
}
