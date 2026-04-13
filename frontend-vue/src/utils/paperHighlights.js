export function normalizePaperHighlights(highlights = []) {
  const valid = highlights
    .filter(item => Number.isFinite(item?.start) && Number.isFinite(item?.end) && item.end > item.start)
    .map(item => ({
      id: item.id,
      start: item.start,
      end: item.end
    }))
    .sort((a, b) => a.start - b.start)

  const merged = []

  for (const item of valid) {
    const previous = merged[merged.length - 1]
    if (!previous || item.start > previous.end) {
      merged.push({ ...item })
      continue
    }

    previous.end = Math.max(previous.end, item.end)
  }

  return merged
}

export function mergePaperHighlight(highlights, start, end, id) {
  const nextStart = Math.min(start, end)
  const nextEnd = Math.max(start, end)
  const overlaps = highlights.filter(item => item.start < nextEnd && item.end > nextStart)
  const remainder = highlights.filter(item => !(item.start < nextEnd && item.end > nextStart))

  if (!overlaps.length) {
    return normalizePaperHighlights([...remainder, { id, start: nextStart, end: nextEnd }])
  }

  const mergedStart = Math.min(nextStart, ...overlaps.map(item => item.start))
  const mergedEnd = Math.max(nextEnd, ...overlaps.map(item => item.end))

  return normalizePaperHighlights([
    ...remainder,
    { id, start: mergedStart, end: mergedEnd }
  ])
}

export function extractCompiledPaperText(text, highlights) {
  const source = text || ''
  const normalized = normalizePaperHighlights(highlights)

  if (!normalized.length) {
    return source.trim()
  }

  return normalized
    .map(item => source.slice(item.start, item.end).trim())
    .filter(Boolean)
    .join('\n\n')
    .trim()
}

export function buildPaperHighlightSegments(text, highlights) {
  const source = text || ''
  const normalized = normalizePaperHighlights(highlights)
  const segments = []
  let cursor = 0

  for (const item of normalized) {
    if (item.start > cursor) {
      segments.push({
        key: `plain-${cursor}-${item.start}`,
        text: source.slice(cursor, item.start),
        highlighted: false,
        id: null
      })
    }

    segments.push({
      key: `mark-${item.id}`,
      text: source.slice(item.start, item.end),
      highlighted: true,
      id: item.id
    })

    cursor = item.end
  }

  if (cursor < source.length) {
    segments.push({
      key: `plain-${cursor}-${source.length}`,
      text: source.slice(cursor),
      highlighted: false,
      id: null
    })
  }

  return segments
}

export function getTextNodeOffset(container, targetNode, nodeOffset) {
  let total = 0
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT)
  let current = null

  while ((current = walker.nextNode())) {
    if (current === targetNode) {
      return total + nodeOffset
    }
    total += current.textContent?.length || 0
  }

  return total
}
