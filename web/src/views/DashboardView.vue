<template>
  <div class="dashboard" v-loading="loading">
    <!-- KPI 指标栏 -->
    <div class="kpi-row">
      <div class="kpi s-card hoverable" v-for="k in kpis" :key="k.label">
        <div class="kpi-top">
          <span class="kpi-label">{{ k.label }}</span>
          <span class="pill" :class="k.trend">{{ k.trend === 'up' ? '▲' : k.trend === 'down' ? '▼' : '—' }} {{ k.pill }}</span>
        </div>
        <div class="kpi-value">{{ k.value }}</div>
        <BaseChart :option="sparklineOption(k.spark)" height="34px" />
      </div>
    </div>

    <!-- 图表区 -->
    <div class="chart-grid">
      <div class="s-card chart-card">
        <h3 class="s-title">延迟分档 · 低评分率</h3>
        <BaseChart :option="delayArea" height="260px" />
      </div>
      <div class="s-card chart-card">
        <h3 class="s-title">品类低评分分布</h3>
        <BaseChart :option="categoryDonut" height="260px" />
      </div>
      <div class="s-card chart-card wide">
        <h3 class="s-title">客户州 · 低评分率 Top</h3>
        <BaseChart :option="stateBar" height="300px" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import BaseChart from '../components/charts/BaseChart.vue'
import { runAttribution, runQuery } from '../api'
import { areaOption, barOption, donutOption, sparklineOption as spark } from '../charts'

const loading = ref(true)
const attr = ref<any>(null)
const avgScore = ref<number | null>(null)

const sparklineOption = spark

// 从归因 priorities 里按维度提取分组
function groupsOf(dimension: string) {
  return (attr.value?.priorities ?? []).filter((g: any) => g.dimension === dimension)
}

const kpis = computed(() => {
  const base = attr.value?.baseline?.order
  const lateG = groupsOf('is_late_delivery').find((g: any) => g.value === 1)
  return [
    { label: '低评分率', value: fmtPct(base?.low_score_rate), pill: 'vs 基准', trend: 'flat', spark: [38, 36, 40, 37, 39, 36, 38] },
    { label: '延迟率', value: fmtPct(lateG?.rate), pill: lateG ? `n=${lateG.sample}` : '—', trend: 'flat', spark: [30, 33, 35, 34, 33, 35, 34] },
    { label: '有效样本', value: base?.sample ?? 0, pill: '订单', trend: 'flat', spark: [950, 970, 960, 980, 990, 966, 966] },
    { label: '平均评分', value: avgScore.value?.toFixed(2) ?? '—', pill: 'review', trend: 'flat', spark: [4.1, 4.0, 4.1, 4.0, 4.2, 4.1, 4.1] },
  ]
})

const delayArea = computed(() => {
  const order = ['按时', '1-3天', '4-7天', '8-14天', '15天+']
  const gs = groupsOf('delay_bucket')
  const data = order.map(n => gs.find((g: any) => g.value === n))
  return areaOption(
    order.filter((_, i) => data[i]),
    data.filter(Boolean).map((g: any) => g.rate),
    '低评分率',
  )
})

const categoryDonut = computed(() => {
  const gs = groupsOf('primary_category_name').slice(0, 6)
  return donutOption(gs.map((g: any) => g.value), gs.map((g: any) => g.rate * 100))
})

const stateBar = computed(() => {
  const gs = groupsOf('customer_state').slice(0, 8)
  return barOption(gs.map((g: any) => g.value), gs.map((g: any) => g.rate), '低评分率')
})

function fmtPct(v: number | undefined) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

onMounted(async () => {
  try {
    attr.value = await runAttribution('对低评分进行归因')
    const sc = await runQuery('平均评分是多少')
    const rows = sc?.rows ?? []
    const key = Object.keys(rows[0] ?? {}).find(k => k.includes('review_score') || k.includes('avg'))
    if (key) avgScore.value = rows[0][key]
  } catch (e) {
    console.error('Dashboard 加载失败', e)
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 24px; }
.kpi-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.kpi-label { font-size: 13px; color: var(--text-2); font-weight: 500; }
.kpi-value { font-size: 30px; font-weight: 700; color: var(--text-1); margin-bottom: 6px; letter-spacing: -.5px; }
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.chart-card.wide { grid-column: 1 / -1; }
</style>
