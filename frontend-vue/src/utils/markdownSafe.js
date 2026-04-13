import DOMPurify from 'dompurify'
import { marked } from 'marked'

export function renderMarkdownSafe(markdown = '') {
  const rawHtml = marked.parse(markdown || '')
  return DOMPurify.sanitize(rawHtml)
}
