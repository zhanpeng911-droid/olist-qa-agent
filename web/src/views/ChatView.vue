<template>
  <div class="chat">
    <div class="chat-body" ref="bodyEl">
      <!-- 空状态：场景化提问矩阵 -->
      <div v-if="!messages.length" class="empty">
        <div class="empty-title">开始你的数据分析</div>
        <div class="empty-sub">选择下方的预设场景，或在底部直接输入自然语言问题</div>
        <div class="prompt-grid">
          <button v-for="p in prompts" :key="p.prompt" class="prompt-card" @click="send(p.prompt)">
            <div class="prompt-icon">{{ p.icon }}</div>
            <div class="prompt-title">{{ p.title }}</div>
            <div class="prompt-desc">{{ p.desc }}</div>
            <span class="prompt-arrow">→</span>
          </button>
        </div>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="avatar" :class="m.role">{{ m.role === 'user' ? '👤' : '🤖' }}</div>
        <div class="bubble">
          <template v-if="m.role === 'user'">{{ m.text }}</template>
          <template v-else>
            <div v-if="m.intent" class="intent-tag">{{ intentLabel(m.intent) }}</div>
            <div v-if="m.running" class="running">{{ m.running }}</div>
            <div v-if="m.text" class="text">{{ m.text }}</div>
            <ResultCard v-if="m.result && m.intent" :intent="m.intent" :d="m.result" />
            <div v-if="m.error" class="error">{{ m.error }}</div>
            <div v-if="m.steps?.length" class="steps">
              <div v-for="(s, j) in m.steps" :key="j" class="step">
                {{ s.event === 'tool' ? `🔧 调用工具 ${s.tool}` : s.event }}
              </div>
            </div>
            <!-- 追问胶囊 -->
            <div v-if="m.suggestions?.length" class="suggestions">
              <span class="suggest-label">继续追问：</span>
              <button v-for="s in m.suggestions" :key="s" class="suggest-chip" @click="send(s)">
                {{ s }}
              </button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 悬浮输入区 -->
    <div class="chat-input">
      <div class="input-shell">
        <span class="src-tag">📎 Olist 数据</span>
        <el-input
          v-model="input"
          placeholder="输入你的问题，例如：对低评分进行归因"
          size="large"
          :disabled="sending"
          class="chat-field"
          @keyup.enter="send"
        />
        <button v-if="messages.length && !sending" class="clear-btn" title="清空上下文" @click="clear">
          <el-icon><Delete /></el-icon>
        </button>
        <button class="send-btn" :disabled="sending || !input.trim()" @click="send">
          <el-icon v-if="!sending"><Promotion /></el-icon>
          <span v-else class="spinner"></span>
        </button>
      </div>
      <div class="input-hint">按 Enter 发送 · 支持归因、统计检验、指标查询等自然语言问题</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { Delete, Promotion } from '@element-plus/icons-vue'
import { chatStream } from '../api'
import ResultCard from '../components/cards/ResultCard.vue'

const input = ref('')
const sending = ref(false)
const messages = ref<any[]>([])
const bodyEl = ref<HTMLDivElement>()

const prompts = [
  { icon: '🔍', title: '归因分析', desc: '对低评分进行归因', prompt: '对低评分进行归因' },
  { icon: '📈', title: '相关性探索', desc: '延迟和低评分是否相关？', prompt: '延迟和低评分是否相关' },
  { icon: '📊', title: '核心指标查询', desc: '总体延迟率和低评分率对比', prompt: '总体延迟率和低评分率是多少' },
  { icon: '🚚', title: '异常排查', desc: '延迟 15 天以上的订单表现', prompt: '延迟 15 天以上的订单低评分率是多少' },
]

const INTENT_LABEL: Record<string, string> = {
  attribution: '归因分析', statistical: '统计检验', query: '指标查询',
  deep_validation: '深度验证', meta: '口径询问', other: '智能对话',
}

function intentLabel(i: string) { return INTENT_LABEL[i] ?? i }

// 按意图给出推荐追问
function suggestionsFor(intent: string): string[] {
  const map: Record<string, string[]> = {
    attribution: ['📊 查看延迟天数的详细评分分布', '🗺️ 查看不同州的关联差异'],
    statistical: ['📈 查看近 6 个月的趋势变化', '🔍 按品类拆分看相关性'],
    query: ['📈 查看该指标的月度趋势', '🧮 对比另一个维度的表现'],
    deep_validation: ['📊 对低评分进行归因', '📈 延迟和低评分是否相关'],
    other: ['📊 对低评分进行归因', '📈 延迟和低评分是否相关'],
  }
  return map[intent] ?? map.other
}

function clear() { messages.value = []; input.value = '' }

async function send(q?: string) {
  const question = (q ?? input.value).trim()
  if (!question || sending.value) return
  input.value = ''
  sending.value = true
  messages.value.push({ role: 'user', text: question })
  const ai: any = { role: 'assistant', steps: [], result: null, intent: '' }
  messages.value.push(ai)
  scrollBottom()

  try {
    await chatStream(
      question,
      (event, data) => {
        if (event === 'intent') ai.intent = data.intent
        else if (event === 'running') ai.running = data.stage
        else if (event === 'result') { ai.result = data; ai.running = '' }
        else if (event === 'step') { ai.steps.push(data); scrollBottom() }
        else if (event === 'answer') ai.text = data.answer
        else if (event === 'warning') ai.warning = data.message
        else if (event === 'error') ai.error = `${data.error}`
        scrollBottom()
      },
      () => {
        ai.suggestions = ai.intent ? suggestionsFor(ai.intent) : []
      },
      (e) => { ai.error = String(e) },
    )
  } finally {
    sending.value = false
  }
}

function scrollBottom() {
  nextTick(() => { bodyEl.value?.scrollTo({ top: bodyEl.value.scrollHeight, behavior: 'smooth' }) })
}
</script>

<style scoped>
.chat { display: flex; flex-direction: column; height: calc(100vh - 128px); }
.chat-body { flex: 1; overflow-y: auto; padding-right: 8px; }

/* 空状态：场景化提问矩阵 */
.empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; }
.empty-title { font-size: 24px; font-weight: 700; color: #0F172A; }
.empty-sub { font-size: 13px; color: var(--text-3); margin-bottom: 22px; }
.prompt-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; max-width: 560px; width: 100%; }
.prompt-card {
  position: relative;
  display: flex; flex-direction: column; align-items: flex-start; gap: 6px;
  padding: 18px 20px; text-align: left;
  background: var(--card); border: 1px solid var(--border-soft);
  border-radius: var(--radius-lg); box-shadow: var(--shadow);
  cursor: pointer; transition: all .2s ease;
}
.prompt-card:hover {
  transform: translateY(-3px); border-color: var(--primary);
  box-shadow: 0 16px 34px -10px rgba(47,101,246,.22);
}
.prompt-icon { font-size: 22px; }
.prompt-title { font-size: 15px; font-weight: 700; color: var(--text-1); }
.prompt-desc { font-size: 12px; color: var(--text-3); }
.prompt-arrow {
  position: absolute; right: 14px; bottom: 12px;
  font-size: 14px; color: var(--text-3); transition: all .2s ease;
}
.prompt-card:hover .prompt-arrow { color: var(--primary); transform: translateX(3px); }

/* 消息 */
.msg { display: flex; gap: 12px; margin-bottom: 22px; }
.msg.user { flex-direction: row-reverse; }
.avatar {
  width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 15px;
}
.msg.assistant .avatar { background: linear-gradient(135deg, var(--sky), var(--primary)); }
.msg.user .avatar { background: #E2E8F0; }
.bubble {
  max-width: 78%; padding: 14px 18px;
  border-radius: var(--radius-lg);
  background: var(--card); box-shadow: var(--shadow);
}
.msg.user .bubble { background: var(--primary); color: #fff; }
.text { line-height: 1.7; font-size: 14px; white-space: pre-wrap; }
.intent-tag {
  display: inline-block; font-size: 11px; color: var(--primary);
  background: #EFF6FF; padding: 2px 10px; border-radius: var(--radius-pill);
  margin-bottom: 8px; font-weight: 600;
}
.running { color: var(--text-3); font-size: 12px; margin-bottom: 6px; }
.error { color: var(--red); font-size: 13px; }
.steps { margin-top: 10px; }
.step { font-size: 11px; color: var(--text-3); line-height: 1.9; }

/* 追问胶囊 */
.suggestions { margin-top: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.suggest-label { font-size: 11px; color: var(--text-3); }
.suggest-chip {
  border: 1px solid var(--border-soft); background: #F8FAFF;
  color: var(--primary); font-size: 12px; padding: 6px 12px;
  border-radius: var(--radius-pill); cursor: pointer; transition: all .18s ease;
}
.suggest-chip:hover { background: #EFF6FF; border-color: var(--primary); }

/* 悬浮输入区 */
.chat-input { margin-top: 14px; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.input-shell {
  display: flex; align-items: center; gap: 10px;
  width: min(760px, 100%);
  background: var(--card); border-radius: var(--radius-pill);
  padding: 8px 10px 8px 18px;
  box-shadow: 0 12px 32px -4px rgba(15, 23, 42, 0.08), 0 6px 18px -6px rgba(47, 101, 246, 0.12);
  border: 1px solid var(--border-soft);
  transition: box-shadow .2s ease, border-color .2s ease;
}
.input-shell:focus-within { border-color: var(--primary); box-shadow: 0 14px 34px -8px rgba(47, 101, 246, 0.26); }
.src-tag {
  font-size: 11px; color: var(--text-2); background: var(--bg);
  padding: 4px 10px; border-radius: var(--radius-pill);
  white-space: nowrap; flex-shrink: 0;
}
.chat-field :deep(.el-input__wrapper) { box-shadow: none !important; background: transparent; padding: 0; }
.clear-btn {
  width: 34px; height: 34px; border-radius: 50%; border: none; cursor: pointer;
  background: var(--bg); color: var(--text-3); flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
}
.clear-btn:hover { color: var(--red); background: var(--red-bg); }
.send-btn {
  width: 42px; height: 42px; border-radius: 50%; border: none; cursor: pointer;
  background: linear-gradient(135deg, #2563EB, var(--primary));
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 18px; flex-shrink: 0; transition: all .2s ease;
  box-shadow: 0 6px 14px -4px rgba(47, 101, 246, 0.5);
}
.send-btn:hover:not(:disabled) { transform: scale(1.05); }
.send-btn:active:not(:disabled) { transform: scale(.96); }
.send-btn:disabled { opacity: .45; cursor: not-allowed; }
.spinner {
  width: 16px; height: 16px; border: 2px solid rgba(255,255,255,.4);
  border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.input-hint { font-size: 11px; color: var(--text-3); }
</style>
