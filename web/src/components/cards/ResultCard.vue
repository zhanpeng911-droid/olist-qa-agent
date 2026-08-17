<template>
  <div class="result-card">
    <div v-if="renderError" class="rc-error">⚠ 结果渲染出错：{{ renderError }}</div>

    <!-- ============ 归因结果 ============ -->
    <template v-if="intent === 'attribution'">
      <div class="rc-row">
        <div class="rc-metric"><span>订单级低评分率</span><b>{{ fmtPct(d.baseline?.order?.low_score_rate) }}</b></div>
        <div class="rc-metric"><span>卖家级低评分率</span><b>{{ fmtPct(d.baseline?.seller?.low_score_rate) }}</b></div>
        <div class="rc-metric"><span>延迟 OR</span><b>{{ fmtNum(d.verification?.evidence?.or) }}</b></div>
        <div class="rc-metric"><span>证据分级</span><b>{{ d.verification?.evidence?.grade ?? '—' }}</b></div>
      </div>

      <div v-if="summary" class="callout">
        <span class="callout-label">💡 核心洞察</span>
        <span class="callout-text">{{ summary }}</span>
      </div>

      <template v-if="priorityRows.length">
        <h4 class="rc-title">优先级问题对象</h4>
        <DataTable :rows="priorityRows" value-key="低评分率" show-rank />
      </template>

      <template v-if="terms.length">
        <h4 class="rc-title">调整后 Logistic OR（森林图）</h4>
        <BaseChart :option="forest" height="220px" />
      </template>

      <template v-if="recs.length">
        <h4 class="rc-title">改善建议（基于已验证证据）</h4>
        <div v-for="r in recs" :key="r.factor" class="rec">
          <div class="rec-head">
            <span class="pill" :class="r.priority === 'P0' ? 'down' : 'flat'">{{ r.priority }}</span>
            <b>{{ r.factor }}</b>
            <span class="rec-resp">{{ r.responsibility }}</span>
          </div>
          <div class="rec-body">
            <div><b>动作：</b>{{ (r.actions || []).join('；') }}</div>
            <div><b>监控：</b>{{ (r.monitor_metrics || []).join('、') }}</div>
            <div><b>验证：</b>{{ r.verify }}</div>
          </div>
        </div>
      </template>

      <div v-if="d.caveats?.length" class="rc-caveat">{{ d.caveats.join('；') }}</div>
    </template>

    <!-- ============ 统计结果 ============ -->
    <template v-else-if="intent === 'statistical'">
      <div class="rc-row">
        <div class="rc-metric"><span>方法</span><b>{{ d.method_label ?? '—' }}</b></div>
        <div class="rc-metric"><span>p 值</span><b>{{ fmtP(d.p_adjusted ?? d.p) }}</b></div>
        <div class="rc-metric"><span>效应量</span><b>{{ fmtNum(d.effect_size ?? d.or) }}</b></div>
        <div class="rc-metric"><span>显著</span><b>{{ d.significant ? '是' : '否' }}</b></div>
      </div>
      <div v-if="d.conclusion" class="callout">
        <span class="callout-label">💡 核心结论</span>
        <span class="callout-text">{{ d.conclusion }}</span>
      </div>
      <p v-if="d.method_reason" class="rc-caveat">{{ d.method_reason }}</p>
      <div v-if="topGroups.length">
        <h4 class="rc-title">分组详情</h4>
        <DataTable :rows="topGroups" :value-key="topValueKey" show-rank />
      </div>
    </template>

    <!-- ============ 查询结果 ============ -->
    <template v-else-if="intent === 'query'">
      <div v-if="!heroItems.length && !hasTable" class="query-empty">
        <span class="qe-icon">💡</span>
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
        <span class="callout-label">💡 核心洞察</span>
        <span class="callout-text">{{ summary }}</span>
      </div>
      <p v-if="d.answer && !summary" class="rc-conclusion">{{ d.answer }}</p>

      <!-- 分组结果：表格 / 图表 切换 -->
      <template v-if="hasTable">
        <div class="view-switch">
          <button :class="{ on: view === 'table' }" @click="view = 'table'">📋 表格</button>
          <button :class="{ on: view === 'chart' }" @click="view = 'chart'">📊 图表</button>
        </div>
        <DataTable v-if="view === 'table'" :rows="d.display_rows" :value-key="groupValueKey" show-rank />
        <BaseChart v-else-if="groupChart" :option="groupChart" height="280px" />
      </template>

      <!-- 执行明细折叠 -->
      <el-collapse v-if="d.sql" class="rc-detail">
        <el-collapse-item title="🔍 查看分析逻辑与 SQL 执行明细">
          <div class="detail-line">来源表：<b>{{ tableLabel(d.table) }}</b>（{{ d.table }}）</div>
          <div class="detail-line">执行模式：<b>{{ modeLabel(d.execution_mode) }}</b> · 数据行 {{ d.row_count ?? 0 }}</div>
          <div class="rc-sql">{{ d.sql }}</div>
        </el-collapse-item>
      </el-collapse>
    </template>

    <div v-else class="rc-caveat">（暂无结构化渲染，请查看上方文本回答）</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onErrorCaptured, ref } from 'vue'
import BaseChart from '../charts/BaseChart.vue'
import DataTable from './DataTable.vue'
import { barOption, forestOption } from '../../charts'
import { fmtNum, fmtP } from '../../format'

const props = defineProps<{ intent: string; d: any }>()

const renderError = ref('')
onErrorCaptured((err) => {
  renderError.value = String((err as any)?.message ?? err)
  return false
})

function fmtPct(v: number | undefined) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

// ---------- attribution ----------
const terms = computed(() => {
  const lo = props.d?.verification?.logistic?.order?.terms ?? []
  return lo.filter((t: any) => !t.term.startsWith('C(') || t.term.includes('is_late_delivery'))
})
const forest = computed(() =>
  forestOption(terms.value.slice(0, 8).map((t: any) => ({ ...t, term: shortTerm(t.term) }))),
)
const recs = computed(() => props.d?.recommendations?.recommendations ?? [])
const priorityRows = computed(() => {
  // priorities → 业务中文行
  const dimLabel: Record<string, string> = {
    is_late_delivery: '是否延迟', delay_bucket: '延迟分档', customer_state: '客户州',
    primary_category_name: '品类', primary_payment_type: '支付方式', order_month: '月份',
    seller_state: '卖家州', route: '线路', cross_state: '是否跨州', is_multi_seller_order: '多卖家',
  }
  return (props.d?.priorities ?? []).map((g: any) => ({
    '维度': dimLabel[g.dimension] ?? g.dimension,
    '对象': g.value === 1 ? '是' : g.value === 0 ? '否' : g.value,
    '样本': g.sample,
    '低评分率': fmtPct(g.low_score_rate),
    'Lift': g.lift?.toFixed(2),
    '超额': g.excess_low_score,
  }))
})

// ---------- statistical ----------
const topGroups = computed(() => props.d?.top_groups ?? [])
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

// 洞察摘要金句（分组结果：最高/最低）
const summary = computed(() => {
  if (props.intent === 'attribution') {
    const ev = props.d?.verification?.evidence
    if (ev && ev.or) {
      return `延迟订单的低评分风险为按时订单的 ${fmtNum(ev.or)} 倍（证据分级：${ev.grade ?? '—'}），是当前数据中最稳定的低评分驱动因素。`
    }
    return ''
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

// 分组图表（横向条形）
const groupChart = computed(() => {
  if (!hasTable.value || !groupValueKey.value) return null
  const vk = groupValueKey.value
  const rows = props.d.display_rows
  const dimKey = Object.keys(rows[0]).find(k => k !== vk) ?? ''
  const labels = rows.slice(0, 10).map(r => String(r[dimKey]))
  const values = rows.slice(0, 10).map(r => (parseNum(r[vk]) ?? 0) / 100)
  return barOption(labels, values, vk)
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
function shortTerm(t: string) {
  if (t.includes('is_late_delivery')) return 'is_late_delivery'
  if (t.startsWith('C(')) return t.replace(/^C\((.*?)\)\[T\.(.*?)\]$/, '$1=$2').slice(0, 26)
  return t.slice(0, 26)
}
</script>

<style scoped>
.result-card { margin-top: 8px; }
.rc-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.rc-metric { background: var(--bg); border-radius: var(--radius-md); padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.rc-metric span { font-size: 12px; color: var(--text-3); }
.rc-metric b { font-size: 18px; font-weight: 700; color: var(--text-1); }
.rc-title { font-size: 14px; font-weight: 600; margin: 14px 0 10px; color: var(--text-2); }
.rc-conclusion { color: var(--text-1); font-size: 14px; line-height: 1.7; }
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
.rec { border: 1px solid var(--border-soft); border-radius: var(--radius-md); padding: 12px 14px; margin-bottom: 10px; }
.rec-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.rec-resp { color: var(--primary); font-size: 12px; margin-left: auto; }
.rec-body { font-size: 13px; color: var(--text-2); line-height: 1.8; }
</style>
