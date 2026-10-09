import { FormEvent, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { ACCOUNT_TOKEN_KEY, AccountUser, ApiError, MembershipOrder, MembershipPlan, api } from '../api/client'

export function AccountEntry() {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [user, setUser] = useState<AccountUser | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [billingOpen, setBillingOpen] = useState(false)
  const [billingBusy, setBillingBusy] = useState(false)
  const [billingError, setBillingError] = useState('')
  const [plans, setPlans] = useState<MembershipPlan[]>([])
  const [selectedPlan, setSelectedPlan] = useState('')
  const [provider, setProvider] = useState<'alipay' | 'wechat'>('alipay')
  const [order, setOrder] = useState<MembershipOrder | null>(null)
  const [accountMode, setAccountMode] = useState<'local_test' | 'cloud' | 'cloud_unconfigured' | null>(null)
  const [restoreBusy, setRestoreBusy] = useState(false)
  const [restoreError, setRestoreError] = useState('')

  const restoreSession = async () => {
    const token = sessionStorage.getItem(ACCOUNT_TOKEN_KEY)
    if (!token) return
    setRestoreBusy(true)
    try {
      const result = await api.accountMe(token)
      if (sessionStorage.getItem(ACCOUNT_TOKEN_KEY) !== token) return
      setUser(result.user)
      setRestoreError('')
    } catch (cause) {
      if (sessionStorage.getItem(ACCOUNT_TOKEN_KEY) !== token) return
      if (cause instanceof ApiError && cause.status === 401) {
        sessionStorage.removeItem(ACCOUNT_TOKEN_KEY)
        setRestoreError('登录已失效，请重新登录。')
      } else {
        // A temporary cloud outage must not destroy a valid stored session.
        setRestoreError('暂时无法验证账号；登录凭证已保留，请稍后重试。')
      }
    } finally {
      setRestoreBusy(false)
    }
  }

  useEffect(() => {
    void api.accountMode().then(result => setAccountMode(result.mode)).catch(() => setAccountMode(null))
    void restoreSession()
  }, [])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); setBillingOpen(false) }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open])

  useEffect(() => {
    if (!billingOpen || !user) return
    let alive = true
    setBillingBusy(true)
    setBillingError('')
    void api.membershipPlans().then(result => {
      if (!alive) return
      setPlans(result)
      setSelectedPlan(current => current || result[0]?.code || '')
    }).catch(cause => {
      if (alive) setBillingError(cause instanceof Error ? cause.message : String(cause))
    }).finally(() => { if (alive) setBillingBusy(false) })
    return () => { alive = false }
  }, [billingOpen, user?.id])

  const createOrder = async () => {
    const token = sessionStorage.getItem(ACCOUNT_TOKEN_KEY)
    if (!token || !selectedPlan) return
    setBillingBusy(true)
    setBillingError('')
    try {
      const result = await api.createMembershipOrder(token, provider, selectedPlan)
      if (!result.pay_url || !/^https:\/\//i.test(result.pay_url)) {
        throw new Error('支付通道未返回可安全打开的 HTTPS 付款页面，请联系支持')
      }
      setOrder(result)
      await window.electronAPI.openExternalHttps(result.pay_url)
    } catch (cause) {
      setBillingError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBillingBusy(false)
    }
  }

  const refreshOrder = async () => {
    const token = sessionStorage.getItem(ACCOUNT_TOKEN_KEY)
    if (!token || !order) return
    setBillingBusy(true)
    setBillingError('')
    try {
      const current = await api.membershipOrder(token, order.order_id)
      setOrder(current)
      const profile = await api.accountMe(token)
      setUser(profile.user)
    } catch (cause) {
      setBillingError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBillingBusy(false)
    }
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    if (accountMode === 'cloud_unconfigured' || accountMode === null) {
      setError('云端账户服务尚未接通，请更新安装包后重试。')
      return
    }
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
      sessionStorage.setItem(ACCOUNT_TOKEN_KEY, result.token)
      setUser(result.user)
      setRestoreError('')
      setPassword('')
      setConfirm('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  const logout = async () => {
    const token = sessionStorage.getItem(ACCOUNT_TOKEN_KEY)
    setBusy(true)
    try {
      if (token) await api.logoutAccount(token)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      sessionStorage.removeItem(ACCOUNT_TOKEN_KEY)
      setUser(null)
      setBusy(false)
    }
  }

  return <>
    <button type="button" aria-label={user ? '账户信息' : '登录或注册'} onClick={() => { setOpen(true); setBillingOpen(false); setError('') }}
      className="h-7 max-w-28 truncate rounded-[4px] border border-border/[0.14] px-2.5 text-[11px] text-foreground/85 hover:border-accent/60 hover:text-accent">
      {user ? `账户 · ${user.phone.slice(0, 3)}****${user.phone.slice(-4)}` : '登录 / 注册'}
    </button>
    {open && createPortal(<div className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 p-4"
      onPointerDown={event => { if (event.target === event.currentTarget) setOpen(false) }}>
      <section role="dialog" aria-modal="true" aria-label="账户" className="w-full max-w-sm rounded-lg border border-border/25 bg-background-elev p-4 text-foreground shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{user ? billingOpen ? '会员充值' : '我的账户' : mode === 'register' ? accountMode === 'local_test' ? '注册本机测试账户' : accountMode === 'cloud' ? '注册云端账户' : '注册账户' : '登录'}</h2>
          <button type="button" aria-label="关闭账户窗口" onClick={() => { setOpen(false); setBillingOpen(false) }} className="text-xs text-muted-foreground hover:text-foreground">关闭</button>
        </div>
        {user && billingOpen ? <div className="space-y-3 text-xs">
          <p className="text-muted-foreground">会员版导出视频不叠加俊小白品牌水印。</p>
          {plans.length > 0 && <div className="space-y-1" aria-label="会员套餐">{plans.map(plan =>
            <button key={plan.code} type="button" aria-pressed={selectedPlan === plan.code} onClick={() => setSelectedPlan(plan.code)}
              className={`block w-full rounded border p-2 text-left ${selectedPlan === plan.code ? 'border-accent text-accent' : 'border-border/25'}`}>
              {plan.title} · ¥{(plan.amount_fen / 100).toFixed(2)} · {plan.duration_days} 天
            </button>)}</div>}
          <div className="grid grid-cols-2 gap-2" aria-label="支付方式">
            {(['alipay', 'wechat'] as const).map(value =>
              <button key={value} type="button" disabled={!plans.length || billingBusy} aria-pressed={provider === value}
                onClick={() => setProvider(value)} className={`rounded border p-3 disabled:opacity-60 ${provider === value && plans.length ? 'border-accent text-accent' : 'border-border/25 text-muted-foreground'}`}>
                {value === 'alipay' ? '支付宝支付' : '微信支付'}
              </button>) }
          </div>
          {plans.length > 0 && <button type="button" disabled={billingBusy || !selectedPlan} onClick={() => void createOrder()}
            className="w-full rounded bg-accent px-3 py-2 font-semibold text-background disabled:opacity-50">{billingBusy ? '请稍候…' : '前往官方支付页面'}</button>}
          {order && <div className="rounded border border-border/25 p-2 text-muted-foreground">
            <p>订单：{order.order_id} · {order.status === 'paid' ? '已支付' : '待支付/待回调确认'}</p>
            <button type="button" disabled={billingBusy} onClick={() => void refreshOrder()} className="mt-2 text-accent disabled:opacity-50">我已支付，刷新状态</button>
          </div>}
          {billingError && <p role="alert" className="rounded border border-amber-500/25 bg-amber-500/[0.07] p-2 leading-5 text-muted-foreground">
            {billingError}。正式商户订单和回调验签服务未接通时不会扣款，也不会通过个人赞助二维码开通会员。
          </p>}
          {!billingError && !plans.length && <p role="status" className="rounded border border-amber-500/25 bg-amber-500/[0.07] p-2 leading-5 text-muted-foreground">
            {billingBusy ? '正在检查会员支付服务…' : '支付尚未开放：正式商户订单和回调验签服务未接通。目前不会扣款，也不会通过个人赞助二维码开通会员。'}
          </p>}
          <button type="button" onClick={() => setBillingOpen(false)} className="rounded border border-border/25 px-3 py-1.5">返回账户</button>
        </div> : user ? <div className="space-y-3 text-xs">
          <p>手机号：{user.phone}</p>
          <p className="text-muted-foreground">注册时间：{new Date(user.created_at).toLocaleString()}</p>
          <p className="rounded border border-accent/20 bg-accent/[0.06] p-2 text-muted-foreground">
            {user.is_member ? `会员有效期至 ${user.member_until ? new Date(user.member_until).toLocaleString() : '待确认'}；导出无品牌水印。`
              : '当前为免费账户；导出视频会带半透明俊小白水印。'}
          </p>
          <p className="text-[10px] leading-4 text-muted-foreground">{accountMode === 'local_test'
            ? '账号目前保存在本机测试库；本机测试账户不能购买会员。'
            : accountMode === 'cloud'
              ? '账号由云端服务保存；充值是否可用，请以会员充值页的支付服务状态为准。'
              : '暂时无法确认账户服务模式，请稍后重试。'}</p>
          <button type="button" onClick={() => setBillingOpen(true)} className="rounded border border-accent/45 px-3 py-1.5 text-accent">会员充值</button>
          <button type="button" disabled={busy} onClick={() => void logout()} className="rounded bg-accent px-3 py-1.5 text-background disabled:opacity-50">退出登录</button>
        </div> : <>
          {accountMode === 'cloud_unconfigured' && <p role="status" className="mb-3 rounded border border-amber-500/25 bg-amber-500/[0.07] p-2 text-xs leading-5 text-muted-foreground">
            云端账户服务尚未接通，当前版本暂不能注册或登录。请更新安装包后重试。
          </p>}
          {restoreError && <div role="status" className="mb-3 rounded border border-amber-500/25 bg-amber-500/[0.07] p-2 text-xs leading-5 text-muted-foreground">
            {restoreError}
            {sessionStorage.getItem(ACCOUNT_TOKEN_KEY) && <button type="button" disabled={restoreBusy} onClick={() => void restoreSession()} className="ml-2 text-accent disabled:opacity-50">{restoreBusy ? '正在重试…' : '重试检查'}</button>}
          </div>}
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
            <button type="submit" disabled={busy || accountMode === null || accountMode === 'cloud_unconfigured'} className="w-full rounded bg-accent px-3 py-2 text-xs font-semibold text-background disabled:opacity-50">
              {busy ? '请稍候…' : mode === 'register' ? '注册并登录' : '登录'}
            </button>
          </form>
          <p className="mt-3 text-[10px] leading-4 text-muted-foreground">{accountMode === 'local_test'
            ? '仅在本机保存测试账户。手机号未经短信验证；注册成功不代表已创建云端账户。'
            : accountMode === 'cloud'
              ? '账号由云端服务保存。手机号尚未经短信验证，请勿将其视为已核验身份。'
              : accountMode === 'cloud_unconfigured'
                ? '此版本未配置云端账户地址，不会在本机创建测试账户。'
              : '正在确认账户服务模式；请确认后再注册。'}</p>
        </>}
      </section>
    </div>, document.body)}
  </>
}
