import { FormEvent, useEffect, useState } from 'react'
import { AccountUser, api } from '../api/client'

const TOKEN_KEY = 'vm-local-test-account-token'

export function AccountEntry() {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [user, setUser] = useState<AccountUser | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const token = sessionStorage.getItem(TOKEN_KEY)
    if (!token) return
    void api.accountMe(token).then(result => setUser(result.user)).catch(() => {
      sessionStorage.removeItem(TOKEN_KEY)
    })
  }, [])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    const normalizedPhone = phone.trim()
    if (!/^1[3-9]\d{9}$/.test(normalizedPhone)) {
      setError('请输入 11 位中国大陆手机号。')
      return
    }
    if (password.length < 8) {
      setError('密码至少需要 8 位。')
      return
    }
    if (mode === 'register' && password !== confirm) {
      setError('两次输入的密码不一致。')
      return
    }
    setBusy(true)
    try {
      const result = mode === 'register'
        ? await api.registerAccount(normalizedPhone, password)
        : await api.loginAccount(normalizedPhone, password)
      sessionStorage.setItem(TOKEN_KEY, result.token)
      setUser(result.user)
      setPassword('')
      setConfirm('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const logout = async () => {
    const token = sessionStorage.getItem(TOKEN_KEY)
    setBusy(true)
    try {
      if (token) await api.logoutAccount(token)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      sessionStorage.removeItem(TOKEN_KEY)
      setUser(null)
      setBusy(false)
    }
  }

  return <>
    <button type="button" aria-label={user ? '账户信息' : '登录或注册'} onClick={() => { setOpen(true); setError('') }}
      className="h-7 max-w-28 truncate rounded-[4px] border border-border/[0.14] px-2.5 text-[11px] text-foreground/85 hover:border-accent/60 hover:text-accent">
      {user ? `账户 · ${user.phone.slice(0, 3)}****${user.phone.slice(-4)}` : '登录 / 注册'}
    </button>
    {open && <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4"
      onPointerDown={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section role="dialog" aria-modal="true" aria-label="账户" className="w-full max-w-sm rounded-lg border border-border/25 bg-background-elev p-4 text-foreground shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{user ? '我的账户' : mode === 'register' ? '注册本机测试账户' : '登录'}</h2>
          <button type="button" aria-label="关闭账户窗口" onClick={() => setOpen(false)} className="text-xs text-muted-foreground hover:text-foreground">关闭</button>
        </div>
        {user ? <div className="space-y-3 text-xs">
          <p>手机号：{user.phone}</p>
          <p className="text-muted-foreground">注册时间：{new Date(user.created_at).toLocaleString()}</p>
          <p className="rounded border border-accent/20 bg-accent/[0.06] p-2 text-muted-foreground">当前为本机 SQLite 测试账户，尚未接入云端数据库或短信验证；剪辑功能无需登录。</p>
          <button type="button" disabled={busy} onClick={() => void logout()} className="rounded bg-accent px-3 py-1.5 text-background disabled:opacity-50">退出登录</button>
        </div> : <>
          <div className="mb-3 flex gap-1" role="tablist" aria-label="账户操作">
            {(['login', 'register'] as const).map(value => <button key={value} type="button" role="tab" aria-selected={mode === value}
              onClick={() => { setMode(value); setPassword(''); setConfirm(''); setError('') }}
              className={`rounded px-3 py-1.5 text-xs ${mode === value ? 'bg-accent text-background' : 'border border-border/20 text-muted-foreground'}`}>
              {value === 'login' ? '登录' : '注册'}
            </button>)}
          </div>
          <form onSubmit={event => void submit(event)} className="space-y-3">
            <label className="block text-xs">手机号
              <input required type="tel" inputMode="numeric" autoComplete="tel" value={phone} onChange={event => setPhone(event.target.value)}
                aria-label="账户手机号" placeholder="11 位中国大陆手机号" className="mt-1 block h-9 w-full rounded border border-border/20 bg-background px-2 text-foreground outline-none focus:border-accent" />
            </label>
            <label className="block text-xs">密码
              <input required type="password" autoComplete={mode === 'register' ? 'new-password' : 'current-password'} value={password} onChange={event => setPassword(event.target.value)}
                aria-label="账户密码" placeholder="至少 8 位" className="mt-1 block h-9 w-full rounded border border-border/20 bg-background px-2 text-foreground outline-none focus:border-accent" />
            </label>
            {mode === 'register' && <label className="block text-xs">确认密码
              <input required type="password" autoComplete="new-password" value={confirm} onChange={event => setConfirm(event.target.value)}
                aria-label="确认账户密码" className="mt-1 block h-9 w-full rounded border border-border/20 bg-background px-2 text-foreground outline-none focus:border-accent" />
            </label>}
            {!!error && <p role="alert" className="text-xs text-hot">{error}</p>}
            <button type="submit" disabled={busy} className="w-full rounded bg-accent px-3 py-2 text-xs font-semibold text-background disabled:opacity-50">
              {busy ? '请稍候…' : mode === 'register' ? '注册并登录' : '登录'}
            </button>
          </form>
          <p className="mt-3 text-[10px] leading-4 text-muted-foreground">仅在本机保存测试账户。手机号未经短信验证；注册成功不代表已创建云端账户。</p>
        </>}
      </section>
    </div>}
  </>
}
