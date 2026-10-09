import { useId, useRef } from 'react'
import { createPortal } from 'react-dom'

export const HELP: Record<string, string> = {
  hook: '每条视频的首段，可多选视频文件，或选整个文件夹。随机混剪按设置的秒数范围或原素材时长选取；口播逻辑先转录原声，Hook 可由不同原片的完整话段合成一整段，预检只要求产品一致。',
  body: '随机混剪从所选文件夹抽取后段，也可按 Body 1→4 分组。口播逻辑使用普通 Body，先转录原声，再选与 Hook 同一产品且台词自然承接的完整话段。',
  bgm: '可选。留空不加配乐，不影响原声或配音。普通模式抽取足够长的音频切片；按 BGM 时长模式随机抽一条完整音频，从头播放到尾，只有一条就使用该条。“Body 开”表示仅 Body 生效，“Body 关”表示全片生效。',
  voice: '可选。从目录随机选一条配音，从开头播放；不足部分保持静音。“Body 开”表示从 Hook 结束后开始，关闭则从视频开头开始。',
  srt: '可以选择单个带时间轴的 SRT 文件，也可以选择包含多个 SRT 文件的目录。目录模式会随机选一份字幕；时间轴超出成片的部分会截断。Body 开表示字幕时间轴整体从 Hook 结束后开始。',
  watermark: '可选图片或 GIF 水印，叠加到画面中央。Body 开表示不覆盖 Hook。请使用有授权的素材。',
  output: '成品保存位置；每款会使用对应的输出目录。任务完成后，可在“产出”页打开视频或所在文件夹。',
  首段: '填写 Hook 最短和最长秒数，软件在范围内选择裁切时长；两端相同即固定秒数。勾选“按原素材时长”后忽略此范围，保留每个 Hook 的完整长度。',
  后段: '填写普通 Body 每段最短和最长秒数；两端相同即固定秒数。“按原素材时长”开启后忽略此范围，随机抽完整原片，按实际时长累计。按 BGM 时长时，最后一段仍可能裁短。',
  片段: '普通模式的总片段数，包含 1 个 Hook。按 BGM 时长生成时自动计算，原手动设置保留，切回后恢复。',
  话段数: '口播逻辑模式每条成片使用 2–6 个带时间码的完整话段；完整 Hook 加一段连贯 Body 即可成片，Hook 也可由多个同主题话段拼成一整段。',
  数量: '目标输出数量。不会因为 BGM 文件数量增加而自动增加；Hook 可用切片不足时可能下调产量，详情见日志。',
  并发: '同时编码的视频数。过高可能耗尽显存或内存，硬件资源不足时会降低并发或回退 CPU；不是越高越快。',
  Hook: 'Hook 原素材裁切重叠率，填写 0～1。完整 Hook 模式忽略此数值；固定时长模式填 1 时允许反复选用首段切片。',
  Body: 'Body 原素材裁切重叠率，填写 0～1。0 表示切片起点相隔一整段；数值越大，可选起点越密。不是视频转场时长。“按原素材时长”的组忽略此值，全部开启时此项置灰。',
  'BGM-R': '普通模式的 BGM 裁切重叠率，填写 0～1。未选 BGM 或按完整 BGM 时长生成时不使用此参数。',
  Hook声: 'Hook 原声音量。0% 静音，100% 保持原音量；有多个音轨时会混合。',
  Body声: 'Body 原声音量。0% 静音，100% 保持原音量。不选 BGM 不会自动改变原声音量。',
  BGM: '背景音乐音量。0% 静音，但所选 BGM 仍可用于决定视频时长。',
  分辨率: '输出画面的宽×高，例如 1080*1920 是竖屏。素材会等比缩放并裁切铺满画面，分辨率越高通常越耗时。',
  码率: '视频编码的数据量，例如 8000k。更高通常文件更大；不能恢复原素材已丢失的细节。',
  帧率: '每秒画面数量，例如 24、30 或 30000/1001。高帧率通常增加编码负担；按音频匹配允许一帧以内的对齐误差。',
  gpu: '自动尝试可用的硬件编码器。不可用时回退 CPU，原因显示在日志。字幕、缩放等处理仍可能使用 CPU，GPU 不一定满载。',
  cover: '独立于去重变换，从成片随机抽帧、缩放裁切作为首帧。替换不改变时长；插入增加一帧并同步后移音频。上传平台是否用首帧作封面由平台决定。',
  variants: '对最终画面进行裁切、色彩、帧混合等变换。轻度变化小，标准较均衡，增强变化更明显。不会修改源文件，也不保证平台的原创或 AI 识别结果。',
  subtitle: '字幕位置控制在画面中的上下位置；字号按画面高度比例计算，字体为内置思源黑体。拖动时显示简易预览，松开消失，实际排版取决于字幕长度。',
  finished: '成品 Hook 保护：一键让 BGM、配音、字幕、水印仅作用于 Body，并把 Hook 原声设为 100%。各素材行仍可单独调整作用范围。',
  duration: '按片段数量：使用手动段数。按 BGM 时长：每条输出随机抽一条完整音频，多余 Body 裁尾，不足按组循环补齐。仅用于 Body 时总时长为 Hook＋BGM；全片时以 BGM 为目标。Hook 更长时保留 Hook 并提示音乐未覆盖，仍可生成。',
  actions: '预检产能：检查素材和预计产量；口播逻辑还会显示素材转录与首个编排方案，不生成成片。启动渲染：开始输出。清除记录：清除已使用 Hook 切片记录，不删素材。停止：停止运行任务。智能压测：用当前配置生成测试样片并比较并发速度，会使用计算资源。',
}

export function FeatureHelp({ topic, title, tutorial = false }: { topic?: string; title?: string; tutorial?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const id = useId()
  const heading = tutorial ? '简易教程' : `${title || topic}说明`
  function open() {
    const el = dialog.current
    if (!el) return
    el.showModal()
    if (!tutorial) {
      const box = trigger.current!.getBoundingClientRect()
      el.style.margin = '0'
      el.style.left = `${Math.max(12, Math.min(box.left, window.innerWidth - el.offsetWidth - 12))}px`
      el.style.top = `${Math.max(12, Math.min(box.bottom + 8, window.innerHeight - el.offsetHeight - 12))}px`
    }
  }
  return <>
    <button ref={trigger} type="button" className={tutorial ? 'tutorial-trigger' : 'feature-help-trigger'}
      aria-label={heading} aria-haspopup="dialog" onClick={open}>{tutorial ? '简易教程' : <span className="feature-help-glyph">?</span>}</button>
    {createPortal(<dialog ref={dialog} className={`feature-help-dialog ${tutorial ? 'tutorial-dialog' : ''}`}
      aria-labelledby={id} onClose={() => trigger.current?.focus()}
      onClick={event => { if (event.target === event.currentTarget) {
        const r = event.currentTarget.getBoundingClientRect()
        if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.current?.close()
      } }}>
      <div className="help-heading"><h2 id={id}>{heading}</h2><button type="button" aria-label="关闭说明" onClick={() => dialog.current?.close()}>关闭</button></div>
      {tutorial ? <div className="tutorial-content">
        <section><h3>先选“你想让我怎么做？”</h3><p>口播逻辑：转录原声，按同一产品接好 Hook 与 Body；随机混剪：按时长抽镜头；独立去重变换：处理已有视频；短视频链接一键去水印：解析已授权的公开分享链接并保存可用源流；成片转字幕：离线识别并生成带时间轴的 SRT。选定后只填写对应任务的素材和参数。</p></section>
        <section><h3>1. 普通混剪</h3><p>选择 Hook 视频或文件夹、Body 文件夹和输出目录 → 设置首段、后段秒数范围与总片段数 → 填输出数量 → 预检 → 启动渲染。范围两端填相同数字即固定秒数。BGM 可留空，原声和配音独立设置。</p></section>
        <section><h3>2. 按顺序分组</h3><p>切换 Body“分组”，启用需要的组，为每组选择目录、片段数和单段时长。输出顺序：Hook → Body 1 → Body 2 → Body 3 → Body 4，组内随机。</p></section>
        <section><h3>3. 给完整音乐配视频</h3><p>选择 BGM → 开启“按 BGM 时长”。每条输出随机使用一条完整音频，Body 自动裁尾或补足；只有一首就始终用它。素材不足会提示，不会无限重复同一切片。</p><p>“Body 开”：总时长 = Hook + BGM；“Body 关”：全片以 BGM 为目标。Hook 已超过音乐时保留 Hook，提示后仍执行。</p></section>
        <section><h3>4. 保护已经做好的 Hook</h3><p>点击“成品 Hook”，再按需勾选“按原素材时长”。保留完整首段与原声，配乐、字幕等只从 Body 开始。</p></section>
        <section><h3>5. 多款批量处理</h3><p>Hook 选择包含多个款式子目录的根目录；普通 Body 选择相同根目录，或包含同名款式子目录的另一个根目录。软件按款拆任务。同名 Body 子目录找不到时会使用整个所选 Body 目录，请先预检确认。分组 Body 使用各组指定目录，不自动按款匹配。</p></section>
        <section><h3>6. 口播逻辑（测试版v0.1）</h3><p>请先整理好对应的完整Hook首段和Body后段的文件夹内容；核对产品是否一致、台词是否通顺。该板块目前还在测试中，使用过程如遇到问题请点击上方“联系我”获取联系方式。设置 2–6 个话段并保留原声，预检后在“口播预览”核对每源转录和 Hook→Body 台词顺序。</p></section>
        <section><h3>7. 输出与排错</h3><p>选择输出目录，按需开启字幕、GPU、随机首帧。日志查看裁尾、补齐、硬件降级与错误原因，产出页打开文件。各项旁边的 ? 可查看说明。</p></section>
      </div> : <p>{HELP[topic || ''] || '请根据素材与输出需求设置此项。'}</p>}
    </dialog>, document.body)}
  </>
}
