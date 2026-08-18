<template>
  <div class="chat">
    <div class="chat-body" ref="bodyEl">
      <!-- 空状态：场景化提问矩阵 -->
      <div v-if="!messages.length" class="empty">
        <div class="empty-title">开始你的数据分析</div>
        <div class="empty-sub">选择下方的预设场景，或在底部直接输入自然语言问题</div>
        <div class="prompt-grid">
          <button v-for="p in prompts" :key="p.prompt" class="prompt-card" @click="send(p.prompt)">
            <component :is="p.icon" :size="22" :stroke-width="1.6" class="prompt-icon" />
            <div class="prompt-title">{{ p.title }}</div>
            <div class="prompt-desc">{{ p.desc }}</div>
            <ArrowRight :size="14" class="prompt-arrow" />
          </button>
        </div>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div v-if="m.role === 'assistant'" class="avatar ai-avatar"><Sparkles :size="15" /></div>
        <div v-else class="avatar user-avatar"><User :size="15" :stroke-width="2" /></div>
        <div class="bubble">
          <template v-if="m.role === 'user'">{{ m.text }}</template>
          <template v-else>
            <div v-if="m.intent" class="intent-tag">{{ intentLabel(m.intent) }}</div>
            <div v-if="m.running" class="running">{{ m.running }}</div>
            <MarkdownText v-if="m.text" :text="m.text" />
            <div v-if="m.summary && !m.result" class="hist-summary">{{ m.summary }}</div>
            <ResultCard
              v-if="m.result && m.intent"
              :intent="m.intent"
              :d="m.result"
              :suggestions="m.suggestions"
              @followup="send"
            />
            <div v-if="m.error" class="error">{{ m.error }}</div>
            <div v-if="m.steps?.length" class="steps">
              <div v-for="(s, j) in m.steps" :key="j" class="step">
                <Wrench v-if="s.event === 'tool'" :size="11" /> {{ s.event === 'tool' ? `调用工具 ${s.tool}` : s.event }}
              </div>
            </div>
            <!-- 意图澄清 -->
            <div v-if="m.clarify?.length" class="suggestions">
              <span class="suggest-label">请问按哪个维度对比？</span>
              <button v-for="c in m.clarify" :key="c.prompt" class="suggest-chip" @click="send(c.prompt)">
                {{ c.label }}
              </button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 悬浮输入区 -->
    <div class="chat-input">
      <div class="input-shell">
        <span class="src-tag"><Paperclip :size="12" /> Olist 数据</span>
        <el-input
          v-model="input"
          placeholder="输入你的问题，例如：对低评分进行归因"
          size="large"
          :disabled="sending"
          class="chat-field"
          @keyup.enter="send"
        />
        <button v-if="messages.length && !sending" class="clear-btn" title="清空当前对话" @click="clear">
          <el-icon><Delete /></el-icon>
        </button>
        <button class="send-btn" :disabled="sending || !input.trim()" @click="send">
          <Send v-if="!sending" :size="18" />
          <span v-else class="spinner"></span>
        </button>
      </div>
      <div class="input-hint">按 Enter 发送 · 支持归因、统计检验、指标查询等自然语言问题</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Delete } from '@element-plus/icons-vue'
import {
  AlertTriangle, ArrowRight, BarChart3, Paperclip, Search, Send, Sparkles, TrendingUp, User, Wrench,
} from 'lucide-vue-next'
import { chatStream } from '../api'
import MarkdownText from '../components/cards/MarkdownText.vue'
import ResultCard from '../components/cards/ResultCard.vue'
import { useSessions } from '../composables/useSessions'

const route = useRoute()
const { sessions, currentId, switchSession, getMessages, setMessages, setTitle, loadSessions } = useSessions()

const input = ref('')
const sending = ref(false)
const messages = ref<any[]>([])
const bodyEl = ref<HTMLDivElement>()

// ---------- 会话消息序列化（小结果完整存，大结果降级存摘要） ----------
function fmtPct(v: number | undefined | null) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}
function summarizeResult(intent: string, d: any): string {
  if (intent === 'query') {
    const r = d?.display_rows?.[0]
    if (r) return Object.entries(r).map(([k, v]) => `${k} ${v}`).join('，')
    return d?.answer || ''
  }
  if (intent === 'attribution') {
    const base = d?.baseline?.order?.low_score_rate
    const ev = d?.verification?.evidence
    return `低评分率 ${fmtPct(base)} · 延迟 OR ${ev?.or?.toFixed?.(2) ?? '—'}（${ev?.grade ?? '—'}）`
  }
  if (intent === 'statistical') return d?.conclusion || ''
  return d?.answer || ''
}
// 完整存储结果（供历史会话恢复表格/图表）——数据库 LONGTEXT 无大小限制，全部整份入库
function serializeMessages(msgs: any[]) {
  return msgs.map((m) => {
    if (m.role === 'user') return { role: 'user', text: m.text }
    const s: any = { role: 'assistant', intent: m.intent, error: m.error, text: m.text || '' }
    if (m.result) {
      s.summary = summarizeResult(m.intent, m.result)
      s.result = m.result
    }
    return s
  })
}
function hydrateMessages(list: any[]): any[] {
  return list.map((m) => m.role === 'user'
    ? { role: 'user', text: m.text }
    : { role: 'assistant', intent: m.intent, error: m.error, text: m.text, summary: m.summary, steps: [], result: m.result ?? null, clarify: [], suggestions: m.result ? suggestionsFor(m.intent) : [] })
}
async function loadCurrent() {
  messages.value = hydrateMessages(await getMessages())
  scrollBottom()
}

// 侧边栏切换会话时同步（currentId 变化）
watch(currentId, () => { loadCurrent() })
// 路由 session 参数变化（侧边栏点击 → ?session=id）也触发加载（双保险）
watch(() => route.query.session, (sid) => {
  if (sid && sid !== currentId.value) {
    switchSession(String(sid)).then(loadCurrent)
  } else if (sid && sid === currentId.value) {
    loadCurrent()
  }
})

// ---------- 预设 ----------
const prompts = [
  { icon: Search, title: '归因分析', desc: '对低评分进行归因', prompt: '对低评分进行归因' },
  { icon: TrendingUp, title: '相关性探索', desc: '延迟和低评分是否相关？', prompt: '延迟和低评分是否相关' },
  { icon: BarChart3, title: '核心指标查询', desc: '总体延迟率和低评分率对比', prompt: '总体延迟率和低评分率是多少' },
  { icon: AlertTriangle, title: '异常排查', desc: '延迟 15 天以上的订单表现', prompt: '延迟 15 天以上的订单低评分率是多少' },
]

const INTENT_LABEL: Record<string, string> = {
  attribution: '归因分析', statistical: '统计检验', query: '指标查询',
  deep_validation: '深度验证', meta: '口径询问', other: '智能对话',
}
function intentLabel(i: string) { return INTENT_LABEL[i] ?? i }

function suggestionsFor(intent: string): { label: string; prompt: string; icon?: string }[] {
  const map: Record<string, { label: string; prompt: string; icon?: string }[]> = {
    attribution: [
      { label: '查看各州分布', prompt: '各客户州的低评分率对比', icon: 'map' },
      { label: '查看品类对比', prompt: '各商品品类的低评分率对比', icon: 'chart' },
    ],
    statistical: [
      { label: '查看月度趋势', prompt: '查看低评分率的月度趋势', icon: 'trend' },
      { label: '查看品类对比', prompt: '各商品品类的低评分率对比', icon: 'chart' },
    ],
    query: [
      { label: '查看月度趋势', prompt: '查看低评分率的月度趋势', icon: 'trend' },
      { label: '查看各州分布', prompt: '各客户州的低评分率对比', icon: 'map' },
    ],
    deep_validation: [
      { label: '查看各州分布', prompt: '各客户州的低评分率对比', icon: 'map' },
      { label: '查看品类对比', prompt: '各商品品类的低评分率对比', icon: 'chart' },
    ],
    other: [
      { label: '对低评分进行归因', prompt: '对低评分进行归因', icon: 'spark' },
      { label: '延迟与低评分相关吗', prompt: '延迟和低评分是否相关', icon: 'trend' },
    ],
  }
  return map[intent] ?? map.other
}

// ---------- 发送 ----------
async function send(q?: unknown) {
  const question = (typeof q === 'string' && q ? q : input.value).trim()
  if (!question || sending.value) return
  input.value = ''
  sending.value = true
  const firstMsg = !messages.value.length
  messages.value.push({ role: 'user', text: question })
  const ai: any = { role: 'assistant', steps: [], result: null, intent: '' }
  messages.value.push(ai)
  if (firstMsg) await setTitle(currentId.value, question.slice(0, 18))
  scrollBottom()

  try {
    await chatStream(
      question,
      (event, data) => {
        if (event === 'intent') ai.intent = data.intent
        else if (event === 'running') ai.running = data.stage
        else if (event === 'result') {
          ai.result = data
          ai.running = ''
          const vague = /另一个维度|其他维度|别的维度|换个维度|对比别的|换一个|别的指标|另一种/.test(question)
          const emptyQ = ai.intent === 'query' && !(data?.display_rows?.length) && !(data?.rows?.length)
          if (vague || emptyQ) {
            ai.clarify = [
              { label: '按客户所在州对比', prompt: '各客户州的低评分率对比' },
              { label: '按支付方式对比', prompt: '各支付方式的低评分率对比' },
              { label: '按商品品类对比', prompt: '各商品品类的低评分率对比' },
            ]
          }
        }
        else if (event === 'step') { ai.steps.push(data); scrollBottom() }
        else if (event === 'answer') ai.text = data.answer
        else if (event === 'warning') ai.warning = data.message
        else if (event === 'error') ai.error = `${data.error}`
        scrollBottom()
      },
      () => {
        ai.suggestions = ai.intent ? suggestionsFor(ai.intent) : []
        setMessages(serializeMessages(messages.value))
      },
      (e) => { ai.error = String(e); setMessages(serializeMessages(messages.value)) },
    )
  } finally {
    sending.value = false
  }
}

function clear() {
  messages.value = []
  setMessages([])
}

function scrollBottom() {
  nextTick(() => { bodyEl.value?.scrollTo({ top: bodyEl.value.scrollHeight, behavior: 'smooth' }) })
}

onMounted(async () => {
  // 侧边栏传入的会话 id
  await loadSessions()
  const sid = route.query.session as string | undefined
  if (sid && sessions.value.some((s) => s.id === sid)) await switchSession(sid)
  await loadCurrent()
  const q = route.query.q as string | undefined
  if (q && q.trim()) send(q)
})
</script>

<style scoped>
/* 悬浮输入区：绝对定位覆盖在消息之上，不挤压消息可视区 */
.chat { position: relative; height: calc(100vh - 128px); }
.chat-body { height: 100%; overflow-y: auto; padding: 0 8px 120px; box-sizing: border-box; }

/* 空状态 */
.empty { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; }
.empty-title { font-size: 24px; font-weight: 700; color: #0F172A; line-height: 1.5; }
.empty-sub { font-size: 13px; color: var(--text-3); margin-bottom: 22px; line-height: 1.5; }
.prompt-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; max-width: 560px; width: 100%; }
.prompt-card {
  position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: 6px;
  padding: 18px 20px; text-align: left;
  background: var(--card); border: 1px solid var(--border-soft);
  border-radius: var(--radius-lg); box-shadow: var(--shadow);
  cursor: pointer; transition: all .2s ease;
}
.prompt-card:hover { transform: translateY(-3px); border-color: var(--primary); box-shadow: 0 16px 34px -10px rgba(47,101,246,.22); }
.prompt-icon { color: var(--primary); }
.prompt-title { font-size: 15px; font-weight: 700; color: var(--text-1); }
.prompt-desc { font-size: 12px; color: var(--text-3); }
.prompt-arrow { position: absolute; right: 16px; bottom: 14px; color: var(--text-3); transition: all .2s ease; }
.prompt-card:hover .prompt-arrow { color: var(--primary); transform: translateX(3px); }

/* 消息 */
.msg { display: flex; gap: 12px; margin-bottom: 22px; }
.msg.user { flex-direction: row-reverse; }
.avatar { width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
.ai-avatar { background: linear-gradient(135deg, #3B82F6, #6366F1); color: #fff; }
.user-avatar {
  background: linear-gradient(135deg, #E0E7FF, #DBEAFE); color: #4338CA;
  font-weight: 700; font-size: 13px;
}
.bubble { max-width: 78%; padding: 14px 18px; border-radius: var(--radius-lg); background: var(--card); box-shadow: var(--shadow); }
.msg.user .bubble { background: var(--primary); color: #fff; }
.intent-tag { display: inline-block; font-size: 11px; color: var(--primary); background: #EFF6FF; padding: 2px 10px; border-radius: var(--radius-pill); margin-bottom: 8px; font-weight: 600; }
.running { color: var(--text-3); font-size: 12px; margin-bottom: 6px; }
.error { color: var(--red); font-size: 13px; }
.steps { margin-top: 10px; }
.step { font-size: 11px; color: var(--text-3); line-height: 1.9; display: flex; align-items: center; gap: 5px; }
.hist-summary { background: var(--bg); border-radius: var(--radius-md); padding: 10px 14px; font-size: 13px; color: var(--text-2); line-height: 1.6; }

/* 追问/澄清 */
.suggestions { margin-top: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.suggest-label { font-size: 11px; color: var(--text-3); }
.suggest-chip { border: 1px solid var(--border-soft); background: #F8FAFF; color: var(--primary); font-size: 12px; padding: 6px 12px; border-radius: var(--radius-pill); cursor: pointer; transition: all .18s ease; }
.suggest-chip:hover { background: #EFF6FF; border-color: var(--primary); }

/* 悬浮输入区 */
.chat-input {
  position: absolute; left: 0; right: 0; bottom: 0;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 14px 0 10px;
  /* 自下而上的淡出，让消息滚动到输入区下方时自然过渡 */
  background: linear-gradient(to top, var(--bg) 78%, rgba(244, 247, 251, 0));
}
.input-shell { display: flex; align-items: center; gap: 10px; width: min(760px, 100%); background: var(--card); border-radius: var(--radius-pill); padding: 8px 10px 8px 18px; box-shadow: 0 12px 32px -4px rgba(15,23,42,.08), 0 6px 18px -6px rgba(47,101,246,.12); border: 1px solid var(--border-soft); transition: box-shadow .2s ease, border-color .2s ease; }
.input-shell:focus-within { border-color: var(--primary); box-shadow: 0 14px 34px -8px rgba(47,101,246,.26); }
.src-tag { font-size: 11px; color: var(--text-2); background: var(--bg); padding: 4px 10px; border-radius: var(--radius-pill); white-space: nowrap; flex-shrink: 0; display: inline-flex; align-items: center; gap: 5px; }
.chat-field :deep(.el-input__wrapper) { box-shadow: none !important; background: transparent; padding: 0; }
.clear-btn { width: 34px; height: 34px; border-radius: 50%; border: none; cursor: pointer; background: var(--bg); color: var(--text-3); flex-shrink: 0; display: flex; align-items: center; justify-content: center; }
.clear-btn:hover { color: var(--red); background: var(--red-bg); }
.send-btn { width: 42px; height: 42px; border-radius: 50%; border: none; cursor: pointer; background: linear-gradient(135deg, #2563EB, var(--primary)); color: #fff; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: all .2s ease; box-shadow: 0 6px 14px -4px rgba(47,101,246,.5); }
.send-btn:hover:not(:disabled) { transform: scale(1.05); }
.send-btn:active:not(:disabled) { transform: scale(.96); }
.send-btn:disabled { opacity: .45; cursor: not-allowed; }
.spinner { width: 16px; height: 16px; border: 2px solid rgba(255,255,255,.4); border-top-color: #fff; border-radius: 50%; animation: spin .7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.input-hint { font-size: 11px; color: var(--text-3); }
</style>
