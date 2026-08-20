<template>
  <div class="md" v-html="html"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { marked, Renderer } from 'marked'

const props = defineProps<{ text: string }>()

// 转义原始 HTML，防止 LLM 回答 / 会话历史中的恶意 HTML 通过 v-html 注入执行
function escapeHtml(raw: string): string {
  return raw
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// 复用单一 renderer：把 html token 转义为纯文本，阻断 XSS（不污染全局 marked）
const safeRenderer = new Renderer()
safeRenderer.html = (token: { text: string }) => escapeHtml(token.text)

// DeepSeek 回答含 Markdown，渲染为符合设计体系的富文本（原始 HTML 已被转义）
const html = computed(() =>
  marked.parse(props.text || '', { breaks: true, gfm: true, renderer: safeRenderer }),
)
</script>

<style scoped>
.md {
  line-height: 1.75;
  font-size: 14px;
  color: var(--text-1);
  word-break: break-word;
}
.md :deep(p) { margin: 6px 0; }
.md :deep(strong) { color: var(--text-1); font-weight: 700; }
.md :deep(a) { color: var(--primary); text-decoration: none; }
.md :deep(code) {
  background: #EFF6FF; color: var(--primary);
  padding: 2px 6px; border-radius: 5px;
  font-size: 12px; font-family: 'SF Mono', Consolas, monospace;
}
.md :deep(pre) {
  background: #1E2238; color: #E2E8F0;
  padding: 12px 14px; border-radius: 10px;
  overflow-x: auto; margin: 8px 0;
}
.md :deep(pre code) { background: transparent; color: inherit; padding: 0; }
.md :deep(ul), .md :deep(ol) { padding-left: 22px; margin: 6px 0; }
.md :deep(li) { margin: 3px 0; }
.md :deep(h1), .md :deep(h2), .md :deep(h3), .md :deep(h4) {
  margin: 12px 0 6px; color: var(--text-1); font-weight: 700;
  line-height: 1.4;
}
.md :deep(h1) { font-size: 17px; }
.md :deep(h2) { font-size: 16px; }
.md :deep(h3) { font-size: 15px; }
.md :deep(blockquote) {
  border-left: 3px solid var(--primary);
  background: #EFF6FF;
  padding: 8px 12px; border-radius: 8px; margin: 8px 0;
  color: var(--text-2); font-size: 13px;
}
.md :deep(table) { border-collapse: collapse; margin: 8px 0; width: 100%; }
.md :deep(th), .md :deep(td) { border: 1px solid var(--border-soft); padding: 6px 10px; font-size: 13px; }
.md :deep(th) { background: #F8FAFC; font-weight: 600; }
.md :deep(hr) { border: none; border-top: 1px solid var(--border-soft); margin: 12px 0; }
</style>
