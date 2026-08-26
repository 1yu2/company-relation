/** 将助手 Markdown 正文转换为适合朗读的纯文本。 */
export function markdownToSpeechText(markdown = '') {
  let text = String(markdown || '')
  text = text.replace(/```[^\n]*\n([\s\S]*?)```/g, '$1')
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  text = text.replace(/<https?:\/\/[^>]+>/g, '')
  text = text.replace(/<[^>]+>/g, ' ')
  text = text.replace(/^\s{0,3}(#{1,6}|>|[-+*]|\d+[.)])\s+/gm, '')
  text = text
    .split('\n')
    .map((line) => normalizeTableLine(line))
    .filter((line) => line !== null)
    .join('\n')
  text = text.replace(/(`{1,2}|\*\*|__|~~)/g, '')
  text = text.replace(/&nbsp;/g, ' ')
  text = text.replace(/&amp;/g, '&')
  text = text.replace(/&lt;/g, '<').replace(/&gt;/g, '>')
  text = text.replace(/[ \t]+/g, ' ')
  text = text.replace(/\n{3,}/g, '\n\n')
  return text.trim()
}

function normalizeTableLine(line) {
  const trimmed = line.trim()
  if (!trimmed.includes('|')) return line

  const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
  if (cells.length === 0 || cells.every((cell) => /^:?-{3,}:?$/.test(cell))) return null
  return cells.join('，')
}

/** 仅接受完全位于助手正文 DOM 内的浏览器选区。 */
export function getContainedSelectionText(rootElement, selection = window.getSelection?.()) {
  if (!rootElement || !selection || selection.rangeCount === 0 || selection.isCollapsed) return ''
  const range = selection.getRangeAt(0)
  const container = range.commonAncestorContainer
  const element = container.nodeType === Node.ELEMENT_NODE ? container : container.parentElement
  if (!element || !rootElement.contains(element)) return ''
  return selection.toString().trim()
}
