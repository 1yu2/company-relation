import assert from 'node:assert/strict'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import MarkdownIt from 'markdown-it'
import { createServer } from 'vite'

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
let server
let render

before(async () => {
  server = await createServer({ root: webRoot, logLevel: 'silent', server: { middlewareMode: true } })
  const { markdownItCitations } = await server.ssrLoadModule('/src/utils/markdown_preview.js')
  const md = new MarkdownIt({ html: true }).use(markdownItCitations)
  render = (source) => md.render(source, {})
})

after(async () => {
  await server?.close()
})

test('企业尽调引用渲染为角标编号，同一 ID 复用编号并在悬停时显示原始 ID', () => {
  const html = render(
    '注册资本为30299.5606万元［F:fact:aaa］［F:fact:bbb］。\n\n法定代表人为包晓军［F:fact:aaa］，关联方见［R:relation:ccc；F:fact:ddd］。'
  )

  assert.equal(html.includes('［F:'), false)
  assert.match(html, /<sup class="cite-ref" title="F:fact:aaa">1<\/sup><sup class="cite-sep">,<\/sup><sup class="cite-ref" title="F:fact:bbb">2<\/sup>/)
  assert.match(html, /包晓军<sup class="cite-ref" title="F:fact:aaa">1<\/sup>/)
  assert.match(html, /<sup class="cite-ref" title="R:relation:ccc">3<\/sup><sup class="cite-sep">,<\/sup><sup class="cite-ref" title="F:fact:ddd">4<\/sup>/)
})

test('表格单元格内的引用同样渲染为角标，不再单独占一列宽度', () => {
  const html = render('| 股东 | 持股 |\n| --- | --- |\n| 王敏 | 40.61%［F:fact:r1］［F:fact:c1］ |')

  assert.match(html, /<td>40.61%<sup class="cite-ref" title="F:fact:r1">1<\/sup><sup class="cite-sep">,<\/sup><sup class="cite-ref" title="F:fact:c1">2<\/sup><\/td>/)
})

test('角标之间隔着正文时不加逗号', () => {
  const html = render('省份为广东［F:fact:p］，区县为斗门区［F:fact:d］。')

  assert.equal(html.includes('cite-sep'), false)
  assert.match(html, /斗门区<sup class="cite-ref" title="F:fact:d">2<\/sup>/)
})

test('普通全角方括号文本不受影响', () => {
  const html = render('见［附注一］和［F 类说明］。')

  assert.match(html, /见［附注一］和［F 类说明］。/)
  assert.equal(html.includes('cite-ref'), false)
})
