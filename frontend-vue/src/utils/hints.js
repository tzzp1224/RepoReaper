export const HINTS = {
  reportReady: {
    en: '<strong>Report ready.</strong> Switch language to view another version, or click <strong>Reanalyze</strong> to regenerate.',
    zh: '<strong>报告已就绪。</strong> 可以切换语言查看其他版本，或点击 <strong>Reanalyze</strong> 重新生成。'
  },
  canGenerate: {
    en: '<strong>Index found.</strong> Click <strong>Generate</strong> to create a new language version without re-indexing.',
    zh: '<strong>已发现索引。</strong> 点击 <strong>Generate</strong> 可在不重新索引的情况下生成新语言版本。'
  },
  needAnalyze: {
    en: '<strong>New repository.</strong> Click <strong>Analyze</strong> to start indexing and report generation.',
    zh: '<strong>新仓库。</strong> 点击 <strong>Analyze</strong> 开始索引和报告生成。'
  },
  langSwitched: {
    en: '<strong>Switched report language.</strong> Loaded from cache.',
    zh: '<strong>已切换报告语言。</strong> 当前结果来自缓存。'
  },
  langNeedGenerate: {
    en: '<strong>No cached report for this language.</strong> Click <strong>Generate</strong> to create one.',
    zh: '<strong>当前语言暂无缓存报告。</strong> 点击 <strong>Generate</strong> 生成一个。'
  }
}

export function getHint(key, lang = 'en') {
  return HINTS[key]?.[lang] || HINTS[key]?.en || ''
}
