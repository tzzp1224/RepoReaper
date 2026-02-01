/**
 * 双语提示消息配置
 */
export const HINTS = {
  reportReady: {
    en: '<strong>Report ready!</strong> Switch language to view another version, or click <strong>Reanalyze</strong> to regenerate.',
    zh: '<strong>报告已加载！</strong> 可切换语言查看其他版本，或点击 <strong>Reanalyze</strong> 重新生成。'
  },
  canGenerate: {
    en: '<strong>Index found!</strong> Click <strong>Generate</strong> to quickly create a report (no re-indexing needed).',
    zh: '<strong>已有索引！</strong> 点击 <strong>Generate</strong> 可快速生成报告（无需重新索引）。'
  },
  needAnalyze: {
    en: '<strong>New repository.</strong> Click <strong>Analyze</strong> to start code indexing and report generation.',
    zh: '<strong>新仓库。</strong> 点击 <strong>Analyze</strong> 开始代码索引和报告生成。'
  },
  langSwitched: {
    en: '<strong>Switched to English report</strong> (from cache).',
    zh: '<strong>已切换到中文报告</strong>（来自缓存）。'
  },
  langNeedGenerate: {
    en: '<strong>No English report yet.</strong> Click <strong>Generate EN</strong> to create one.',
    zh: '<strong>暂无中文报告。</strong> 点击 <strong>Generate 中文</strong> 快速生成。'
  }
}

/**
 * 获取提示消息
 */
export function getHint(key, lang = 'en') {
  return HINTS[key]?.[lang] || HINTS[key]?.['en'] || ''
}
