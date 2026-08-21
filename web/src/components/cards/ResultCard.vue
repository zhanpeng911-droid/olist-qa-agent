<template>
  <div class="result-card">
    <div v-if="renderError" class="rc-error">⚠ 结果渲染出错：{{ renderError }}</div>

    <!-- ============ 归因结果 ============ -->
    <template v-if="intent === 'attribution'">
      <div class="rc-row">
        <div class="rc-metric"><span>{{ attributionTargetLabel }}发生率</span><b>{{ fmtPct(attributionRate) }}</b></div>
        <div class="rc-metric"><span>第一层筛选通过</span><b>{{ d.significant_features?.length ?? 0 }}</b></div>
        <div class="rc-metric"><span>进入调整模型</span><b>{{ d.selected_features?.length ?? 0 }}</b></div>
        <div class="rc-metric"><span>调整后仍显著</span><b>{{ d.adjusted_features?.length ?? 0 }}</b></div>
      </div>

      <div v-if="summary" class="callout">
        <span class="callout-label"><Lightbulb :size="13" /> 核心洞察</span>
        <span class="callout-text">{{ summary }}</span>
      </div>

      <AttributionDetails :d="d" />

      <div v-if="d.caveats?.length" class="rc-caveat">{{ d.caveats.join('；') }}</div>
    </template>

    <!-- ============ 统计结果 ============ -->
    <template v-else-if="intent === 'statistical'">
      <template v-if="d.batch">
        <div class="rc-row">
          <div class="rc-metric"><span>检验目标</span><b>{{ d.anchor_variable_label ?? '—' }}</b></div>
          <div class="rc-metric"><span>已完成</span><b>{{ d.successful_count ?? 0 }}/{{ d.comparison_count ?? 0 }}</b></div>
          <div class="rc-metric"><span>FDR校正后显著</span><b>{{ d.significant_count ?? 0 }}</b></div>
          <div class="rc-metric"><span>检验前提不足</span><b>{{ d.inconclusive_count ?? 0 }}</b></div>
        </div>
        <div v-if="d.conclusion" class="callout">
          <span class="callout-label"><Lightbulb :size="13" /> 批量检验结论</span>
          <span class="callout-text">{{ d.conclusion }}</span>
        </div>
        <h4 class="rc-title">逐项检验结果</h4>
        <DataTable :rows="batchRows" :limit="20" />
        <p v-if="d.method_reason" class="rc-caveat">{{ d.method_reason }}</p>
      </template>
      <template v-else>
        <div class="rc-row">
          <div class="rc-metric"><span>方法</span><b>{{ d.method_label ?? '—' }}</b></div>
          <div class="rc-metric"><span>p 值</span><b>{{ fmtP(d.p_adjusted ?? d.p) }}</b></div>
          <div class="rc-metric"><span>效应量</span><b>{{ fmtNum(d.effect_size ?? d.or) }}</b></div>
          <div class="rc-metric"><span>显著</span><b>{{ d.significant ? '是' : '否' }}</b></div>
        </div>
        <div v-if="d.conclusion" class="callout">
          <span class="callout-label"><Lightbulb :size="13" /> 核心结论</span>
          <span class="callout-text">{{ d.conclusion }}</span>
        </div>
        <p v-if="d.method_reason" class="rc-caveat">{{ d.method_reason }}</p>
        <div v-if="topGroups.length">
          <h4 class="rc-title">分组详情</h4>
          <DataTable :rows="topGroups" :value-key="topValueKey" show-rank />
        </div>
      </template>
    </template>

    <!-- ============ 查询结果 ============ -->
    <template v-else-if="intent === 'query'">
      <div v-if="!heroItems.length && !hasTable" class="query-empty">
        <Lightbulb :size="16" class="qe-icon" />
        {{ d.error || '未识别到明确的指标，试试：「总体低评分率是多少」或「各客户州的低评分率对比」' }}
      </div>

      <!-- 核心大数字 -->
      <div v-if="heroItems.length" class="query-hero">
        <div class="hero-item" v-for="h in heroItems" :key="h.label">
          <span class="hero-label">{{ h.label }}</span>
          <b class="hero-value">{{ h.value }}</b>
        </div>
      </div>

      <!-- 洞察摘要 -->
      <div v-if="summary" class="callout">
        <span class="callout-label"><Lightbulb :size="13" /> 核心洞察</span>
        <span class="callout-text">{{ summary }}</span>
      </div>
      <p v-if="conclusionText" class="rc-conclusion">{{ conclusionText }}</p>

      <!-- 分组结果：表格 / 图表 切换 -->
      <template v-if="hasTable">
        <div class="view-switch">
          <button :class="{ on: view === 'table' }" @click="view = 'table'"><Table2 :size="13" /> 表格</button>
          <button :class="{ on: view === 'chart' }" @click="view = 'chart'"><BarChart3 :size="13" /> 图表</button>
        </div>
        <DataTable v-if="view === 'table'" :rows="sortedRows" :value-key="groupValueKey" :show-rank="!isTimeline" />
        <BaseChart v-else-if="groupChart" :option="groupChart" height="280px" />
      </template>

      <!-- 执行明细折叠 -->
      <el-collapse v-if="d.sql" class="rc-detail">
        <el-collapse-item title="查看分析逻辑与 SQL 执行明细">
          <div class="detail-line">来源表：<b>{{ tableLabel(d.table) }}</b>（{{ d.table }}）</div>
          <div class="detail-line">执行模式：<b>{{ modeLabel(d.execution_mode) }}</b> · 数据行 {{ d.row_count ?? 0 }}</div>
          <div class="rc-sql">{{ d.sql }}</div>
        </el-collapse-item>
      </el-collapse>

      <!-- 继续追问快捷胶囊 -->
      <div v-if="suggestions?.length" class="followups">
        <span class="fu-label">继续追问</span>
        <button v-for="s in suggestions" :key="s.prompt" class="fu-chip" @click="$emit('followup', s.prompt)">
          <component :is="s.icon === 'trend' ? TrendingUp : s.icon === 'map' ? Map : s.icon === 'chart' ? BarChart3 : s.icon === 'spark' ? Lightbulb : ArrowRight" :size="12" />
          {{ s.label }}
        </button>
      </div>
    </template>

    <!-- ============ 深度验证结果 ============ -->
    <template v-else-if="intent === 'deep_validation'">
      <div class="rc-row">
        <div class="rc-metric"><span>目标</span><b>{{ d.target_label ?? '—' }}</b></div>
        <div class="rc-metric"><span>成功模型</span><b>{{ d.successful_models ?? 0 }}</b></div>
        <div class="rc-metric"><span>显著变量</span><b>{{ sigCount }}</b></div>
        <div class="rc-metric"><span>未能估计</span><b>{{ d.summary?.not_estimated?.length ?? 0 }}</b></div>
      </div>

      <template v-if="featureRows.length">
        <h4 class="rc-title">控制混杂后的验证结果</h4>
        <GenericResult :value="featureRows" />
      </template>

      <div v-if="dvSummary" class="callout">
        <span class="callout-label"><Lightbulb :size="13" /> 验证结论</span>
        <span class="callout-text">{{ dvSummary }}</span>
      </div>

      <div v-if="d.caveats?.length" class="rc-caveat">{{ d.caveats.join('；') }}</div>
    </template>

    <!-- ============ 通用兜底：递归渲染任意结果结构 ============ -->
    <div v-else class="rc-generic">
      <div v-if="d.conclusion || d.answer" class="callout">
        <span class="callout-label"><Lightbulb :size="13" /> 结论</span>
        <span class="callout-text">{{ d.conclusion || d.answer }}</span>
      </div>
      <GenericResult :value="genericValue" />
      <el-collapse v-if="sqlText" class="rc-detail">
        <el-collapse-item title="查看 SQL 执行明细">
          <div class="rc-sql">{{ sqlText }}</div>
        </el-collapse-item>
      </el-collapse>
      <div v-if="caveatText" class="rc-caveat">{{ caveatText }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onErrorCaptured, ref } from 'vue'
import { ArrowRight, BarChart3, Lightbulb, Map, Table2, TrendingUp } from 'lucide-vue-next'
import BaseChart from '../charts/BaseChart.vue'
import AttributionDetails from './AttributionDetails.vue'
import DataTable from './DataTable.vue'
import GenericResult from './GenericResult.vue'
import { barOption } from '../../charts'
import { fmtNum, fmtP, fmtPct } from '../../format'

const props = defineProps<{ intent: string; d: any; suggestions?: { label: string; prompt: string; icon?: string }[] }>()
const emit = defineEmits<{ (e: 'followup', prompt: string): void }>()

const renderError = ref('')
onErrorCaptured((err) => {
  renderError.value = String((err as any)?.message ?? err)
  return false
})

const attributionTargetLabel = computed(() => props.d?.target_short_label ?? '低评分')
const attributionRate = computed(() => props.d?.target_rate
  ?? props.d?.target_baseline?.target_rate
  ?? props.d?.baseline?.order?.low_score_rate)

// ---------- statistical ----------
const topGroups = computed(() => props.d?.top_groups ?? [])
const batchRows = computed(() => (props.d?.results ?? []).map((row: any) => ({
  '变量': row.comparison_label ?? row.variable_y_label ?? '—',
  '检验方法': row.method_label ?? '未完成',
  '原始p值': row.ok ? fmtP(row.p) : '—',
  'FDR p值': row.ok ? fmtP(row.p_adjusted) : '—',
  '效应量': row.effect_text ?? '—',
  '样本量': row.sample ?? '—',
  '结论': row.conclusion ?? row.error ?? '—',
})))
const topValueKey = computed(() => {
  const r = topGroups.value[0]
  if (!r) return ''
  return Object.keys(r).find(k => k !== 'value' && k !== '分组' && k !== 'n' && k !== '样本') ?? ''
})

// ---------- query ----------
const heroItems = computed(() => {
  const row = props.d?.display_rows?.[0]
  if (!props.d?.dimensions?.length && row) {
    return Object.entries(row).map(([label, value]) => ({ label, value: String(value) }))
  }
  return []
})
const hasTable = computed(() => props.d?.dimensions?.length && props.d?.display_rows?.length)
const view = ref('table')

// 分组数值列（用于数据条/图表/摘要）
const groupValueKey = computed(() => {
  const row = props.d?.display_rows?.[0]
  if (!row) return ''
  const dimLabels = new Set((props.d?.dimensions ?? []).map((x: string) => dimLabelOf(x)))
  return Object.keys(row).find(k => !dimLabels.has(k)) ?? ''
})
function dimLabelOf(dim: string): string {
  const m: Record<string, string> = { order_month: '月份', primary_category_name: '品类',
    primary_payment_type: '支付方式', customer_state: '客户州', delay_bucket: '延迟分档',
    is_late_delivery: '是否延迟', route: '线路', seller_state: '卖家州', cross_state: '是否跨州' }
  return m[dim] ?? dim
}
function parseNum(v: any): number | null {
  if (v == null) return null
  const n = parseFloat(String(v).replace('%', '').replace(',', ''))
  return isNaN(n) ? null : n
}

// 是否是时间线（月份）维度：时间正序展示，不显示排名徽章
const isTimeline = computed(() => (props.d?.dimensions ?? []).includes('order_month'))

// 排序后的展示行：
// - 排名表（品类/州等）→ 按数值列降序，30.35% 排第 1
// - 时间线（月份）→ 按时间正序
const sortedRows = computed(() => {
  const rows = props.d?.display_rows ?? []
  if (!rows.length || !groupValueKey.value) return rows
  const vk = groupValueKey.value
  const copy = rows.slice()
  if (isTimeline.value) {
    return copy.sort((a: any, b: any) => {
      const dim = Object.keys(a).find(k => k !== vk) ?? ''
      return String(a[dim]).localeCompare(String(b[dim]))
    })
  }
  return copy.sort((a: any, b: any) => (parseNum(b[vk]) ?? -1) - (parseNum(a[vk]) ?? -1))
})

// 洞察摘要金句（分组结果：最高/最低）
const summary = computed(() => {
  if (props.intent === 'attribution') {
    const ev = props.d?.verification?.evidence
    if (props.d?.target === 'is_low_score' && ev && ev.or) {
      return `延迟订单的低评分风险为按时订单的 ${fmtNum(ev.or)} 倍（证据分级：${ev.grade ?? '—'}），是当前数据中最稳定的低评分驱动因素。`
    }
    const labels = (props.d?.adjusted_features ?? [])
      .filter((row: any) => row?.stable)
      .map((row: any) => row.label ?? row.feature)
    return labels.length
      ? `控制预设变量后，${labels.join('、')}仍与${attributionTargetLabel.value}显著相关；该结果说明调整后的统计关联，不代表因果。`
      : `当前没有候选变量同时通过${attributionTargetLabel.value}归因的两层统计门槛。`
  }
  if (!hasTable.value || !groupValueKey.value) return ''
  const rows = props.d.display_rows
  const vk = groupValueKey.value
  const dimKey = Object.keys(rows[0]).find(k => k !== vk) ?? ''
  const nums = rows.map(r => ({ label: String(r[dimKey]), v: parseNum(r[vk]) })).filter(x => x.v != null)
  if (!nums.length) return ''
  const max = nums.reduce((a, b) => (a.v! > b.v! ? a : b))
  const min = nums.reduce((a, b) => (a.v! < b.v! ? a : b))
  if (max.label === min.label) return `${dimKey}「${max.label}」${vk}为 ${max.v}%`
  return `${dimKey}「${max.label}」${vk}最高（${max.v}%），「${min.label}」最低（${min.v}%），差异 ${(max.v! - min.v!).toFixed(1)} 个百分点。`
})

// 无分组（总体指标）时的加粗业务结论，替代重复的原始 answer 纯文本
const conclusionText = computed(() => {
  if (props.intent !== 'query') return ''
  if (hasTable.value) return ''
  const row = props.d?.display_rows?.[0]
  if (!row) return ''
  const parts = Object.entries(row)
  if (!parts.length) return ''
  const texts = parts.map(([k, v]) => `${k} ${v}`).join('，')
  const k0 = parts[0][0]
  const v0 = String(parts[0][1])
  // 对典型的比率指标生成业务化表述
  if (/率/.test(k0)) {
    return `当前${k0}为 ${v0}，整体表现${(parseFloat(v0) <= 25) ? '处于可控区间' : '偏高，值得关注'}。`
  }
  return texts
})

// 分组图表（横向条形，与表格同序）
const groupChart = computed(() => {
  if (!hasTable.value || !groupValueKey.value) return null
  const vk = groupValueKey.value
  const rows = sortedRows.value
  const dimKey = Object.keys(rows[0]).find(k => k !== vk) ?? ''
  const labels = rows.slice(0, 10).map(r => String(r[dimKey]))
  const values = rows.slice(0, 10).map(r => (parseNum(r[vk]) ?? 0) / 100)
  return barOption(labels, values, vk)
})

// ---------- deep_validation ----------
const featureRows = computed(() => {
  return (props.d?.feature_results ?? []).map((r: any) => ({
    '变量': r.label ?? r.feature,
    '方法': r.method ?? '—',
    '调整后 OR': r.adjusted_or != null ? fmtNum(r.adjusted_or) : '联合 Wald',
    '95%CI': r.ci95 ? `${fmtNum(r.ci95[0])} ~ ${fmtNum(r.ci95[1])}` : '—',
    'p 值': fmtP(r.p_adjusted ?? r.p),
    '显著': r.significant ? '是' : (r.ok === false ? '未估计' : '否'),
  }))
})
const sigCount = computed(() => props.d?.summary?.adjusted_significant?.length ?? 0)
const dvSummary = computed(() => {
  const s = props.d?.summary
  if (!s) return ''
  const parts: string[] = []
  if (s.adjusted_significant?.length) parts.push(`显著：${s.adjusted_significant.join('、')}`)
  if (s.adjusted_not_significant?.length) parts.push(`不显著：${s.adjusted_not_significant.join('、')}`)
  if (s.not_estimated?.length) parts.push(`未能估计：${s.not_estimated.join('、')}`)
  return parts.join('；')
})

// ---------- 通用兜底：提取元信息字段，剩余交给 GenericResult 递归渲染 ----------
const META_SKIP = new Set(['sql', 'sqls', 'caveats', 'note', 'notes', 'load_profile',
  'schema_version', 'analysis_mode', 'recommendations', 'mode', 'grain_note', 'interpretation'])
const genericValue = computed(() => {
  const d = props.d
  if (!d || typeof d !== 'object' || Array.isArray(d)) return d ?? {}
  const out: Record<string, any> = {}
  for (const [k, v] of Object.entries(d)) {
    if (META_SKIP.has(k)) continue
    out[k] = v
  }
  return out
})
const sqlText = computed(() => {
  if (props.d?.sql) return String(props.d.sql)
  if (Array.isArray(props.d?.sqls) && props.d.sqls.length) return props.d.sqls.join('\n\n')
  return ''
})
const caveatText = computed(() => {
  const c = props.d?.caveats ?? props.d?.note
  if (Array.isArray(c)) return c.join('；')
  if (typeof c === 'string') return c
  return ''
})

// ---------- 映射 ----------
const TABLE_LABEL: Record<string, string> = {
  mart_order_delivery: '订单交付宽表',
  mart_order_seller_delivery: '订单-卖家宽表',
  mart_order_item_analysis: '商品项分析视图',
}
const MODE_LABEL: Record<string, string> = {
  deterministic_query: '确定性查询', react: '智能推理', '确定': '确定性查询',
}
function tableLabel(t?: string) { return TABLE_LABEL[t ?? ''] ?? '—' }
function modeLabel(m?: string) { return MODE_LABEL[m ?? ''] ?? m ?? '—' }
</script>

<style scoped>
.result-card { margin-top: 8px; }
.rc-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.rc-metric { background: var(--bg); border-radius: var(--radius-md); padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.rc-metric span { font-size: 12px; color: var(--text-3); }
.rc-metric b { font-size: 18px; font-weight: 700; color: var(--text-1); }
.rc-title { font-size: 14px; font-weight: 600; margin: 14px 0 10px; color: var(--text-2); }
.rc-conclusion { color: var(--text-1); font-size: 13px; font-weight: 600; line-height: 1.7; margin: 0 0 4px; }
.rc-caveat { color: var(--text-3); font-size: 12px; margin-top: 12px; line-height: 1.6; }
.rc-error { background: var(--red-bg); color: var(--red); padding: 8px 12px; border-radius: var(--radius-sm); font-size: 12px; margin-bottom: 10px; }
.callout { display: flex; flex-direction: column; gap: 4px; background: #EFF6FF; border-left: 3px solid var(--primary); border-radius: var(--radius-md); padding: 12px 14px; margin-bottom: 10px; }
.callout-label { font-size: 12px; font-weight: 700; color: var(--primary); }
.callout-text { font-size: 13px; color: var(--text-1); line-height: 1.7; }
.query-hero { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 12px; }
.hero-item { background: var(--bg); border-radius: var(--radius-md); padding: 14px 18px; display: flex; flex-direction: column; gap: 4px; min-width: 120px; }
.hero-label { font-size: 12px; color: var(--text-3); }
.hero-value { font-size: 26px; font-weight: 700; color: var(--text-1); letter-spacing: -.5px; }
.query-empty { display: flex; align-items: center; gap: 8px; background: #FFF7ED; border: 1px solid #FED7AA; border-radius: var(--radius-md); padding: 12px 14px; font-size: 13px; color: #92400E; margin-bottom: 10px; }
.qe-icon { font-size: 16px; }
.view-switch { display: inline-flex; gap: 4px; background: var(--bg); border-radius: var(--radius-pill); padding: 3px; margin-bottom: 8px; }
.view-switch button { border: none; background: transparent; padding: 5px 14px; border-radius: var(--radius-pill); font-size: 12px; color: var(--text-2); cursor: pointer; transition: all .15s ease; }
.view-switch button.on { background: #fff; color: var(--primary); font-weight: 600; box-shadow: var(--shadow); }
.rc-detail { margin-top: 12px; border: 1px solid var(--border-soft); border-radius: var(--radius-md); overflow: hidden; }
.detail-line { font-size: 12px; color: var(--text-2); margin-bottom: 6px; }
.rc-sql { font-size: 12px; color: var(--text-3); background: var(--bg); border-radius: var(--radius-sm); padding: 10px 12px; word-break: break-all; font-family: 'SF Mono', Consolas, monospace; }
.followups { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
.fu-label { font-size: 11px; color: var(--text-3); }
.fu-chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--border-soft); background: #F8FAFF; color: var(--primary); font-size: 12px; padding: 6px 12px; border-radius: var(--radius-pill); cursor: pointer; transition: all .18s ease; }
.fu-chip:hover { background: #EFF6FF; border-color: var(--primary); }
.fu-chip svg { flex-shrink: 0; }
</style>
