<template>
  <div class="chat">
    <div class="chat-body" ref="bodyEl">
      <el-empty v-if="!messages.length" description="输入问题开始分析，例如：对低评分进行归因" />

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

    <div class="chat-input">
      <el-input
        v-model="input"
        placeholder="例如：对低评分进行归因 / 延迟和评分是否相关 / 总体延迟率是多少"
        size="large"
        :disabled="sending"
        @keyup.enter="send"
      >
        <template #append>
          <el-button type="primary" :icon="Promotion" :loading="sending" @click="send" style="border-radius: 0 12px 12px 0">
            发送
          </el-button>
        </template>
      </el-input>
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

const INTENT_LABEL: Record<string, string> = {
  attribution: '归因分析',
  statistical: '统计检验',
  query: '指标查询',
  deep_validation: '深度验证',
  meta: '口径询问',
  other: '智能对话',
}

function intentLabel(i: string) { return INTENT_LABEL[i] ?? i }

async function send() {
  const q = input.value.trim()
  if (!q || sending.value) return
  input.value = ''
  sending.value = true
  messages.value.push({ role: 'user', text: q })
  const ai: any = { role: 'assistant', steps: [], result: null, intent: '' }
  messages.value.push(ai)
  scrollBottom()

  await chatStream(
    q,
    (event, data) => {
      if (event === 'intent') { ai.intent = data.intent }
      else if (event === 'running') { ai.running = data.stage }
      else if (event === 'result') { ai.result = data; ai.running = '' }
      else if (event === 'step') { ai.steps.push(data); scrollBottom() }
      else if (event === 'answer') { ai.text = data.answer }
      else if (event === 'warning') { ai.warning = data.message }
      else if (event === 'error') { ai.error = `${data.error}` }
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
  background: rgba(47,101,246,.08); padding: 2px 10px; border-radius: var(--radius-pill);
  margin-bottom: 8px; font-weight: 600;
}
.running { color: var(--text-3); font-size: 12px; margin-bottom: 6px; }
.error { color: var(--red); font-size: 13px; }
.steps { margin-top: 10px; }
.step { font-size: 11px; color: var(--text-3); line-height: 1.9; }
.chat-input { margin-top: 18px; }
</style>
