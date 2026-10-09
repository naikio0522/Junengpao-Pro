import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

const alipayQr = new URL('../assets/alipay-sponsor.jpg', import.meta.url).href
const wechatPayQr = new URL('../assets/wechat-pay-sponsor.jpg', import.meta.url).href

type PaymentMethod = 'alipay' | 'wechat'

export function SponsorMe() {
  const [open, setOpen] = useState(false)
  const [method, setMethod] = useState<PaymentMethod>('alipay')
  const buttonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        buttonRef.current?.focus()
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [open])

  const close = () => {
    setOpen(false)
    buttonRef.current?.focus()
  }

  return (
    <>
      <button ref={buttonRef} type="button" onClick={() => setOpen(true)}
        aria-label="赞助我" aria-haspopup="dialog" aria-expanded={open}
        className="h-7 shrink-0 rounded-[4px] border border-border/[0.14] bg-foreground/[0.02] px-2.5 text-[11px] text-foreground hover:border-accent/60 hover:text-accent">
        赞助我
      </button>
      {open && createPortal(<div className="fixed inset-0 z-[1100] flex items-center justify-center bg-black/60 p-4"
        onPointerDown={event => { if (event.target === event.currentTarget) close() }}>
        <div role="dialog" aria-modal="true" aria-label="赞助我"
          className="flex max-h-[calc(100vh-32px)] w-[min(480px,calc(100vw-32px))] flex-col overflow-hidden rounded-lg border border-border/20 bg-background-elev p-4 text-foreground shadow-2xl">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">赞助我</h2>
              <p className="mt-1 text-[11px] text-muted-foreground">如果这个工具帮到了你，可以扫码支持后续完善。</p>
            </div>
            <button type="button" onClick={close} aria-label="关闭赞助面板"
              className="rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted/30 hover:text-foreground">关闭</button>
          </div>
          <div className="mt-3 grid shrink-0 grid-cols-2 gap-1 rounded border border-border/15 bg-foreground/[0.03] p-1" role="tablist" aria-label="赞助方式">
            <button type="button" role="tab" aria-selected={method === 'alipay'} onClick={() => setMethod('alipay')}
              className={`rounded px-2 py-1.5 text-xs ${method === 'alipay' ? 'bg-accent font-semibold text-background' : 'text-muted-foreground hover:text-foreground'}`}>支付宝</button>
            <button type="button" role="tab" aria-selected={method === 'wechat'} onClick={() => setMethod('wechat')}
              className={`rounded px-2 py-1.5 text-xs ${method === 'wechat' ? 'bg-accent font-semibold text-background' : 'text-muted-foreground hover:text-foreground'}`}>微信支付</button>
          </div>
          <div role="tabpanel" className="mt-3 min-h-0 overflow-y-auto">
            <img src={method === 'alipay' ? alipayQr : wechatPayQr}
              alt={method === 'alipay' ? '支付宝赞助二维码' : '微信支付赞助二维码'}
              className="mx-auto block h-auto w-full max-w-[400px] rounded bg-white object-contain"
              style={{ maxHeight: 'min(700px, max(96px, calc(100dvh - 220px)))' }} />
            <p className="mt-2 text-center text-xs text-muted-foreground">
              {method === 'alipay' ? '打开支付宝扫码' : '打开微信扫码支付'}
            </p>
          </div>
        </div>
      </div>, document.body)}
    </>
  )
}
