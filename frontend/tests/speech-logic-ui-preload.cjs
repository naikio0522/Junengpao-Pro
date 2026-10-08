localStorage.setItem('vm-config', JSON.stringify({
  selection_mode: 'speech_logic', hook_dir: 'C:\\clips\\hook', body_dirs: ['C:\\clips\\body'],
  base_out_dir: 'C:\\clips\\output',
  total_clips: 5, vol_hook_orig: 80, vol_orig: 80,
}))

window.__speechPreflightRequests = []
window.fetch = async (url, options = {}) => {
  const path = String(url)
  if (path.endsWith('/preflight')) {
    window.__speechPreflightRequests.push(JSON.parse(options.body))
    if (window.__speechFallback) {
      return new Response(JSON.stringify({
        ok: true, capacity: 1,
        report: [{ name: '牙膏', ok: true, capacity: 1, message: '有风险但可渲染；需人工复核', speech_logic_preview: {
          fallback: true, fallback_mode: 'clip_level', sku_id: '', topic_id: '', capacity: 1,
          hook_duration_s: 2, hook_segment_count: 1, transcript: '',
          warnings: ['产品无法确认；请核对画面与原声'],
          transcripts: [{ source_file: 'C:\\clips\\hook\\a.mp4', source_type: 'hook', status: 'blocked',
            reasons: ['没有可用口播转录；可提供同名带时间码 SRT/TXT'], text: '', cues: [] }],
          segments: [
            { role: 'hook', source_file: 'C:\\clips\\hook\\a.mp4', start_s: 0, end_s: 2, text: '', sku_id: '', topic_id: '' },
            { role: 'body', source_file: 'C:\\clips\\body\\b.mp4', start_s: 0, end_s: 2, text: '', sku_id: '', topic_id: '' },
          ],
        } }],
      }), { headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({
      ok: true, capacity: 2,
      report: [{ name: '牙膏', ok: true, capacity: 2, message: '可产出: 2 个', speech_logic_preview: {
        sku_id: '99', topic_id: 'sensitivity', capacity: 2,
        hook_duration_s: 4.2, hook_segment_count: 2,
        transcript: '喝凉水牙酸？因为牙本质暴露。用这支牙膏保护敏感牙。',
        warnings: ['请人工复核画面包装'],
        transcripts: [
          { source_file: 'C:\\clips\\hook\\a.mp4', source_type: 'hook', status: 'review', reasons: ['句末由规则推断，请复听'], source_unit_count: 2, usable_unit_count: 2, sku_id: '99', topic_id: 'sensitivity', text: '喝凉水牙酸？因为牙本质暴露。', cues: [
            { start_s: 0, end_s: 2, text: '喝凉水牙酸？' }, { start_s: 2, end_s: 4.2, text: '因为牙本质暴露。' },
          ] },
          { source_file: 'C:\\clips\\body\\b.mp4', source_type: 'body', text: '用这支牙膏保护敏感牙。', cues: [
            { start_s: 1, end_s: 4, text: '用这支牙膏保护敏感牙。' },
          ] },
        ],
        segments: [
          { role: 'hook', source_file: 'C:\\clips\\hook\\a.mp4', start_s: 0, end_s: 2, text: '喝凉水牙酸？', sku_id: '99', topic_id: 'sensitivity', pain_id: 'cold_sensitivity', mechanism_id: 'dentin' },
          { role: 'hook', source_file: 'C:\\clips\\hook\\a.mp4', start_s: 2, end_s: 4.2, text: '因为牙本质暴露。', sku_id: '99', topic_id: 'sensitivity', pain_id: 'cold_sensitivity', mechanism_id: 'dentin' },
          { role: 'body', source_file: 'C:\\clips\\body\\b.mp4', start_s: 1, end_s: 4, text: '用这支牙膏保护敏感牙。', sku_id: '99', topic_id: 'sensitivity', pain_id: 'cold_sensitivity', mechanism_id: 'dentin' },
        ],
      } }],
    }), { headers: { 'Content-Type': 'application/json' } })
  }
  const data = path.endsWith('/health') ? { status: 'ok' } : path.endsWith('/tasks') ? [] : { files: [], count: 0 }
  return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
}
