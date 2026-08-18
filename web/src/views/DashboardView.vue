<template>
  <div class="dashboard">
    <!-- 加载骨架 -->
    <template v-if="loading">
      <div class="kpi-row">
        <div v-for="i in 4" :key="i" class="s-card kpi"><el-skeleton :rows="3" animated /></div>
      </div>
      <div class="chart-grid">
        <div v-for="i in 2" :key="i" class="s-card"><el-skeleton :rows="6" animated /></div>
      </div>
    </template>

    <template v-else-if="error">
      <el-empty :description="`看板加载失败：${error}`" />
    </template>

    <template v-else>
      <!-- KPI 指标栏 -->
      <div class="kpi-row">
        <div class="s-card kpi hoverable" v-for="k in kpis" :key="k.label">
          <div class="kpi-top">
            <span class="kpi-label">{{ k.label }}</span>
            <span class="pill flat">{{ k.pillText }}</span>
          </div>
          <div class="kpi-value">{{ k.value }}</div>
          <div class="kpi-spark">
            <BaseChart v-if="k.spark && k.spark.length > 1" :option="spark(k.spark)" height="34px" />
            <BaseChart v-else :option="flatLine()" height="34px" />
          </div>
        </div>
      </div>

      <!-- 图表区 -->
      <div class="chart-grid">
        <div class="s-card chart-card">
          <h3 class="s-title">客户州 · 低评分率
            <span v-if="stateRanks.length" class="rank-badges">
              <span v-for="(s, i) in stateRanks" :key="s" class="rank-badge" :class="`r${i + 1}`">{{ i + 1 }} {{ s }}</span>
            </span>
          </h3>
          <BaseChart v-if="stateBar" :option="stateBar" height="260px" />
          <el-empty v-else description="样本不足，无州级分组数据" :image-size="60" />
        </div>
        <div class="s-card chart-card">
          <h3 class="s-title">低评分订单 · 支付方式构成</h3>
          <BaseChart v-if="payDonut" :option="payDonut" height="230px" />
          <el-empty v-else description="样本不足，无支付方式分组" :image-size="60" />
          <!-- 2×2 图例网格（卡片化，拒绝单行横排挤压） -->
          <div v-if="payLegend.length" class="donut-legend">
            <div v-for="(lg, i) in payLegend" :key="lg.label" class="lg-item">
              <span class="lg-dot" :style="{ background: lg.color }"></span>
              <span class="lg-name">{{ lg.label }}</span>
              <span class="lg-nums">
                <span class="lg-count">{{ lg.count }}</span>
                <span class="lg-pct">{{ lg.pct }}</span>
              </span>
            </div>
          </div>
        </div>
        <div class="s-card chart-card wide">
          <h3 class="s-title">低评分率 · 月度趋势</h3>
          <BaseChart v-if="trendArea" :option="trendArea" height="240px" />
          <el-empty v-else description="暂无月度趋势数据" :image-size="60" />
        </div>
      </div>

      <!-- 数据缺口提示条（不占大卡） -->
      <div v-if="notices.length" class="notice-bar">
        <el-icon><InfoFilled /></el-icon>
        <span v-for="n in notices" :key="n">{{ n }}</span>
      </div>

      <p class="data-note">数据来源：低评分关联因素分析（{{ sourceLabel }}）· 口径：低评分 = 评分 ≤ 3 · 有效样本 {{ baseSample }} 单</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { InfoFilled } from '@element-plus/icons-vue'
import BaseChart from '../components/charts/BaseChart.vue'
import { runAttribution, runQuery, getMeta } from '../api'
import { areaOption, barOption, donutOption, sparklineOption as spark, flatLineOption as flatLine } from '../charts'

const loading = ref(true)
const error = ref('')
const attr = ref<any>(null)
const meta = ref<any>(null)
const lateRate = ref<number | null>(null)
const avgScore = ref<number | null>(null)
const trend = ref<any[]>([])

const sourceLabel = computed(() => meta.value?.source_label ?? '演示样本')
const baseSample = computed(() => attr.value?.baseline?.order?.sample ?? 0)

const orderFactors = computed(() => attr.value?.factors?.order ?? [] as any[])
function groupsOf(dim: string) {
  return orderFactors.value.filter((g: any) => g.dimension === dim)
}

// 数据缺口提示（1000 行样本下被门槛过滤的维度）
const notices = computed(() => {
  const list: string[] = []
  if (!groupsOf('delay_bucket').some((g: any) => g.value !== '按时')) {
    list.push('延迟分档：除“按时”外其余档位未达样本门槛(100)，全量数据可完整展示')
  }
  if (!groupsOf('primary_category_name').length) {
    list.push('品类分组：未达样本门槛(100)，未展示')
  }
  return list
})

const kpis = computed(() => {
  const base = attr.value?.baseline?.order
  const lowRate = base?.low_score_rate as number | undefined
  const cleanTrend = trend.value
    .filter((r: any) => (r._m_order_count ?? 100) >= 10)
    .map((r: any) => r._m_low_score_rate)
  return [
    {
      label: '低评分率', value: fmtPct(lowRate),
      pillText: `样本 ${base?.sample ?? 0}`,
      spark: cleanTrend,
    },
    {
      label: '延迟率', value: lateRate.value == null ? '—' : fmtPct(lateRate.value),
      pillText: lateRate.value == null ? '待全量数据' : '全量',
      spark: [],
    },
    {
      label: '有效样本', value: base?.sample ?? 0,
      pillText: '订单级', spark: [],
    },
    {
      label: '平均评分', value: avgScore.value?.toFixed(2) ?? '—',
      pillText: '5 分制', spark: [],
    },
  ]
})

const stateBar = computed(() => {
  const gs = groupsOf('customer_state')
    .slice()
    .sort((a: any, b: any) => (b.low_score_rate ?? 0) - (a.low_score_rate ?? 0))
    .slice(0, 8)
  if (!gs.length) return null
  return barOption(gs.map((g: any) => g.value), gs.map((g: any) => g.low_score_rate), '低评分率', { max: 35 })
})

// Top3 排行徽章（与条形图降序一致）
const stateRanks = computed(() => {
  const gs = groupsOf('customer_state')
    .slice()
    .sort((a: any, b: any) => (b.low_score_rate ?? 0) - (a.low_score_rate ?? 0))
    .slice(0, 3)
  return gs.map((g: any) => g.value)
})

const PAY_LABEL: Record<string, string> = {
  credit_card: '信用卡支付', boleto: 'Boleto 现金券', voucher: '代金券',
  debit_card: '借记卡', not_defined: '未定义',
}
// 与 charts.ts donutOption 的 color 数组保持一致，供 HTML 图例取色
const DONUT_COLORS = ['#2F65F6', '#00C5FF', '#38BDF8', '#10B981', '#F59E0B', '#F43F5E', '#8B5CF6']

// 低评分订单的支付方式构成（计数占比，凑满 100%）
const payDonut = computed(() => {
  const gs = groupsOf('primary_payment_type').slice(0, 6)
  if (!gs.length) return null
  const total = gs.reduce((s, g) => s + (g.low_score_count ?? 0), 0)
  if (!total) return null
  const values = gs.map((g: any) => g.low_score_count ?? 0)
  const labels = gs.map((g: any) => PAY_LABEL[g.value] ?? g.value)
  return donutOption(labels, values, String(total))
})

// 2×2 卡片化图例数据（名称 / 单量 / 占比 / 色块）
const payLegend = computed(() => {
  const gs = groupsOf('primary_payment_type').slice(0, 6)
  const total = gs.reduce((s, g) => s + (g.low_score_count ?? 0), 0)
  if (!total) return []
  return gs.map((g: any, i: number) => ({
    label: PAY_LABEL[g.value] ?? g.value,
    count: (g.low_score_count ?? 0).toLocaleString(),
    pct: `${(((g.low_score_count ?? 0) / total) * 100).toFixed(0)}%`,
    color: DONUT_COLORS[i % DONUT_COLORS.length],
  }))
})

// 过滤冷启动小样本月（如 2016-10 仅 2 单），再画趋势
const trendArea = computed(() => {
  const rows = trend.value
    .filter((r: any) => (r._m_order_count ?? 100) >= 10)
    .slice()
    .sort((a: any, b: any) => (a.order_month < b.order_month ? -1 : 1))
  if (rows.length < 2) return null
  return areaOption(rows.map((r: any) => r.order_month), rows.map((r: any) => r._m_low_score_rate), '低评分率')
})

function fmtPct(v: number | undefined | null) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

onMounted(async () => {
  try {
    const [a, m] = await Promise.all([runAttribution('对低评分进行归因'), getMeta()])
    attr.value = a
    meta.value = m
    const [lr, sc, tr] = await Promise.all([
      runQuery('总体延迟率是多少'),
      runQuery('平均评分是多少'),
      runQuery('各月份低评分率和订单量'),
    ])
    const row0 = lr?.rows?.[0] ?? {}
    const k = Object.keys(row0).find(x => x.includes('late') || x.includes('delay'))
    if (k) lateRate.value = row0[k]
    const scRow = sc?.rows?.[0] ?? {}
    const sk = Object.keys(scRow).find(x => x.includes('review_score') || x.includes('avg'))
    if (sk) avgScore.value = scRow[sk]
    trend.value = tr?.rows ?? []
  } catch (e: any) {
    error.value = String(e?.message ?? e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 24px; }
.kpi-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.kpi-label { font-size: 13px; color: var(--text-2); font-weight: 500; }
.kpi-value { font-size: 30px; font-weight: 700; color: var(--text-1); margin-bottom: 4px; letter-spacing: -.5px; }
.kpi-spark { height: 34px; }
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.chart-card.wide { grid-column: 1 / -1; }

/* 州排行 Top3 徽章（浅蓝底深蓝字） */
.s-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.rank-badges { display: inline-flex; align-items: center; gap: 6px; }
.rank-badge {
  font-size: 11px; font-weight: 700; color: #2563EB;
  background: #EFF6FF; border: 1px solid rgba(47,101,246,.15);
  padding: 2px 9px; border-radius: var(--radius-pill);
}
.rank-badge.r1 { color: #fff; background: linear-gradient(135deg, #2F65F6, #00C5FF); border: none; }
.rank-badge.r2 { color: #4338CA; background: #EEF2FF; }
.rank-badge.r3 { color: #0369A1; background: #E0F2FE; }

/* 支付方式 2×2 卡片化图例网格（严格两列对齐） */
.donut-legend {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px 20px;
  margin-top: 12px; padding: 12px 14px;
  background: var(--bg); border-radius: var(--radius-md);
}
.lg-item { display: flex; align-items: center; gap: 8px; min-width: 0; }
.lg-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.lg-name { font-size: 12px; color: var(--text-2); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.lg-nums { display: flex; align-items: baseline; gap: 8px; flex-shrink: 0; }
.lg-count { font-size: 12px; font-weight: 700; color: var(--text-1); white-space: nowrap; }
.lg-pct { font-size: 12px; font-weight: 600; color: var(--text-3); white-space: nowrap; }

.notice-bar {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin-top: 20px; padding: 10px 16px;
  background: rgba(47,101,246,.05); border-radius: var(--radius-md);
  color: var(--text-2); font-size: 12px;
}
.notice-bar .el-icon { color: var(--primary); }
.data-note { margin-top: 14px; font-size: 12px; color: var(--text-3); text-align: center; }
</style>
