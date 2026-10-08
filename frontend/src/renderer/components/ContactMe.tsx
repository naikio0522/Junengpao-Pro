import { useEffect, useRef, useState } from 'react'

const feishuQr = new URL('../assets/feishu-contact.jpg', import.meta.url).href
const wechatQr = new URL('../assets/wechat-contact.jpg', import.meta.url).href

type ContactMethod = 'wechat' | 'feishu'

export function ContactMe() {
  const [open, setOpen] = useState(false)
  const [method, setMethod] = useState<ContactMethod>('wechat')
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  return (
    <div ref={containerRef} className="relative z-50 shrink-0">
      <button
        type="button"
        aria-label={open ? '隐藏联系方式' : '显示联系方式'}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="flex h-7 items-center gap-2 rounded-[4px] border border-accent/45 bg-accent px-2.5 text-[12px] font-semibold text-background hover:bg-accent-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        联系我
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true" className="h-4 w-4">
          <path d="M2.5 12s3.4-6 9.5-6 9.5 6 9.5 6-3.4 6-9.5 6-9.5-6-9.5-6Z" />
          <circle cx="12" cy="12" r="2.7" />
        </svg>
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="联系我"
          className="absolute left-0 top-full mt-2 overflow-y-auto rounded-lg border border-border/20 bg-background-elev p-3 text-foreground shadow-2xl"
          style={{ width: 'min(420px, calc(100vw - 32px))', maxHeight: 'calc(100vh - 110px)' }}
        >
          <div className="mb-2 flex items-center justify-between gap-2 text-xs">
            <span className="font-semibold">联系我</span>
            <button type="button" onClick={() => setOpen(false)} aria-label="关闭联系方式" className="rounded px-2 py-1 text-muted-foreground hover:bg-muted/30 hover:text-foreground">关闭</button>
          </div>
          <div className="mb-3 grid grid-cols-2 gap-1 rounded border border-border/15 bg-foreground/[0.03] p-1" role="tablist" aria-label="联系方式">
            <button type="button" role="tab" aria-selected={method === 'wechat'} onClick={() => setMethod('wechat')}
              className={`rounded px-2 py-1.5 text-xs ${method === 'wechat' ? 'bg-accent font-semibold text-background' : 'text-muted-foreground hover:text-foreground'}`}>微信</button>
            <button type="button" role="tab" aria-selected={method === 'feishu'} onClick={() => setMethod('feishu')}
              className={`rounded px-2 py-1.5 text-xs ${method === 'feishu' ? 'bg-accent font-semibold text-background' : 'text-muted-foreground hover:text-foreground'}`}>飞书</button>
          </div>
          <div role="tabpanel">
            <img src={method === 'wechat' ? wechatQr : feishuQr}
              alt={method === 'wechat' ? 'NaiKio 的微信联系人二维码' : '徐学志的飞书联系人二维码'}
              className="mx-auto block h-auto w-full max-w-[340px] rounded bg-white object-contain"
              style={{ maxHeight: 'min(580px, max(100px, calc(100dvh - 260px)))' }} />
            <p className="mt-2 text-center text-xs text-muted-foreground">
              {method === 'wechat' ? '微信扫码添加 NaiKio' : '飞书扫码添加徐学志'}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
