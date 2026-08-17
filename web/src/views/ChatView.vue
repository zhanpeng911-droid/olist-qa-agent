<template>
  <div class="chat">
    <div class="chat-body" ref="bodyEl">
      <!-- 空状态：推荐提问胶囊 -->
      <div v-if="!messages.length" class="empty">
        <div class="empty-title">开始你的数据分析</div>
        <div class="empty-sub">从这些问题开始，或直接输入你的问题</div>
        <div class="chips">
          <button v-for="c in prompts" :key="c" class="chip" @click="send(c)">
            {{ c }}
          </button>
        </div>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="avatar" :class="m.role">{{ m.role === 'user' ? '我' : 'AI' }}</div>
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
          </template>
        </div>
      </div>
    </div>

    <!-- 悬浮输入区 -->
    <div class="chat-input">
      <div class="input-shell">
        <el-input
          v-model="input"
          placeholder="输入你的问题，例如：对低评分进行归因"
          size="large"
          :disabled="sending"
          class="chat-field"
          @keyup.enter="send"
        />
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
import { Promotion } from '@element-plus/icons-vue'
import { chatStream } from '../api'
import ResultCard from '../components/cards/ResultCard.vue'

const input = ref('')
const sending = ref(false)
const messages = ref<any[]>([])
const bodyEl = ref<HTMLDivElement>()

const prompts = [
  '📊 对低评分进行归因',
  '📈 延迟和低评分是否相关',
  '🔢 总体延迟率和低评分率是多少',
  '🚚 延迟 15 天以上的订单表现如何',
]

const INTENT_LABEL: Record<string, string> = {
  attribution: '归因分析', statistical: '统计检验', query: '指标查询',
  deep_validation: '深度验证', meta: '口径询问', other: '智能对话',
}

function intentLabel(i: string) { return INTENT_LABEL[i] ?? i }

async function send(q?: string) {
  const question = (q ?? input.value).trim()
  if (!question || sending.value) return
  input.value = ''
  sending.value = true
  messages.value.push({ role: 'user', text: question })
  const ai: any = { role: 'assistant', steps: [], result: null, intent: '' }
  messages.value.push(ai)
  scrollBottom()

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
    () => { sending.value = false },
    (e) => { ai.error = String(e); sending.value = false },
  )
}

function scrollBottom() {
  nextTick(() => { bodyEl.value?.scrollTo({ top: bodyEl.value.scrollHeight, behavior: 'smooth' }) })
}
</script>

<style scoped>
.chat { display: flex; flex-direction: column; height: calc(100vh - 128px); }
.chat-body { flex: 1; overflow-y: auto; padding-right: 8px; }

/* 空状态 */
.empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; }
.empty-title { font-size: 22px; font-weight: 700; color: var(--text-1); }
.empty-sub { font-size: 13px; color: var(--text-3); margin-bottom: 18px; }
.chips { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; max-width: 640px; }
.chip {
  border: 1px solid var(--border-soft); background: var(--card);
  color: var(--text-2); font-size: 13px; font-weight: 500;
  padding: 10px 18px; border-radius: var(--radius-pill);
  cursor: pointer; transition: all .18s ease; box-shadow: var(--shadow);
}
.chip:hover { border-color: var(--primary); color: var(--primary); background: #EFF6FF; transform: translateY(-2px); }

/* 消息 */
.msg { display: flex; gap: 12px; margin-bottom: 22px; }
.msg.user { flex-direction: row-reverse; }
.avatar {
  width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center; font-size: 13px;
}
.msg.assistant .avatar { background: linear-gradient(135deg, var(--sky), var(--primary)); color: #fff; }
.msg.user .avatar { background: var(--bg); color: var(--text-2); }
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

/* 悬浮输入区 */
.chat-input { margin-top: 14px; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.input-shell {
  display: flex; align-items: center; gap: 10px;
  width: min(760px, 100%);
  background: var(--card); border-radius: var(--radius-pill);
  padding: 8px 10px 8px 20px;
  box-shadow: 0 10px 30px -8px rgba(47,101,246,.18), var(--shadow);
  border: 1px solid var(--border-soft);
  transition: box-shadow .2s ease, border-color .2s ease;
}
.input-shell:focus-within { border-color: var(--primary); box-shadow: 0 14px 34px -8px rgba(47,101,246,.28); }
.chat-field :deep(.el-input__wrapper) { box-shadow: none !important; background: transparent; padding: 0; }
.send-btn {
  width: 42px; height: 42px; border-radius: 50%; border: none; cursor: pointer;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-size: 18px; flex-shrink: 0; transition: opacity .2s ease;
  box-shadow: 0 6px 14px -4px rgba(47,101,246,.5);
}
.send-btn:disabled { opacity: .45; cursor: not-allowed; }
.spinner {
  width: 16px; height: 16px; border: 2px solid rgba(255,255,255,.4);
  border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.input-hint { font-size: 11px; color: var(--text-3); }
</style>
