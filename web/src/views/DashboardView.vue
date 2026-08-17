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
            <span class="pill" :class="k.pillClass">{{ k.pillText }}</span>
          </div>
          <div class="kpi-value">{{ k.value }}</div>
          <div class="kpi-spark">
            <BaseChart v-if="k.spark && k.spark.length > 1" :option="spark(k.spark)" height="34px" />
            <span v-else class="no-spark">暂无走势数据</span>
          </div>
        </div>
      </div>

      <!-- 图表区 -->
      <div class="chart-grid">
        <div class="s-card chart-card">
          <h3 class="s-title">客户州 · 低评分率</h3>
          <BaseChart v-if="stateBar" :option="stateBar" height="260px" />
          <el-empty v-else description="样本不足，无州级分组数据" :image-size="60" />
        </div>
        <div class="s-card chart-card">
          <h3 class="s-title">支付方式 · 低评分率</h3>
          <BaseChart v-if="payDonut" :option="payDonut" height="260px" />
          <el-empty v-else description="样本不足，无支付方式分组" :image-size="60" />
        </div>
        <div class="s-card chart-card wide">
          <h3 class="s-title">延迟分档 · 低评分率</h3>
          <BaseChart v-if="delayArea" :option="delayArea" height="260px" />
          <el-empty v-else description="1000 行样本下多数延迟档位未达样本门槛，全量数据可完整展示" :image-size="70" />
        </div>
      </div>

      <p class="data-note">数据来源：低评分关联因素分析（{{ sourceLabel }}）· 口径：低评分 = 评分 ≤ 3，有效样本 = {{ baseSample }} 单</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import BaseChart from '../components/charts/BaseChart.vue'
import { runAttribution, runQuery, getMeta } from '../api'
import { areaOption, barOption, donutOption, sparklineOption as spark } from '../charts'

const loading = ref(true)
const error = ref('')
const attr = ref<any>(null)
const meta = ref<any>(null)
const lateRate = ref<number | null>(null)
const avgScore = ref<number | null>(null)
const trend = ref<any[]>([])   // 月度低评分率趋势（真实 sparkline）

const sparklineOption = spark

const sourceLabel = computed(() => meta.value?.source_label ?? '演示样本')
const baseSample = computed(() => attr.value?.baseline?.order?.sample ?? 0)

// factors 才是按维度分组的数据（priorities 只有 Top 3）
const orderFactors = computed(() => attr.value?.factors?.order ?? [] as any[])
function groupsOf(dim: string) {
  return orderFactors.value.filter((g: any) => g.dimension === dim)
}

const kpis = computed(() => {
  const base = attr.value?.baseline?.order
  const lowRate = base?.low_score_rate as number | undefined
  return [
    {
      label: '低评分率', value: fmtPct(lowRate),
      pillText: `基准 ${fmtPct(lowRate)}`, pillClass: 'flat',
      spark: trend.value.map((r: any) => r._m_low_score_rate),
    },
    {
      label: '延迟率', value: lateRate.value == null ? '—' : fmtPct(lateRate.value),
      pillText: lateRate.value == null ? '待全量数据' : '近 30 天',
      pillClass: lateRate.value == null ? 'flat' : 'up',
      spark: [],
    },
    {
      label: '有效样本', value: base?.sample ?? 0,
      pillText: '订单级', pillClass: 'flat', spark: [],
    },
    {
      label: '平均评分', value: avgScore.value?.toFixed(2) ?? '—',
      pillText: avgScore.value == null ? '待查询' : 'review',
      pillClass: 'flat', spark: [],
    },
  ]
})

const stateBar = computed(() => {
  const gs = groupsOf('customer_state').slice(0, 8)
  if (!gs.length) return null
  return barOption(gs.map((g: any) => g.value), gs.map((g: any) => g.low_score_rate), '低评分率')
})

const payDonut = computed(() => {
  const gs = groupsOf('primary_payment_type').slice(0, 6)
  if (!gs.length) return null
  return donutOption(gs.map((g: any) => g.value), gs.map((g: any) => g.low_score_rate * 100))
})

const delayArea = computed(() => {
  const gs = groupsOf('delay_bucket')
  if (gs.length < 2) return null
  const order = ['按时', '1-3天', '4-7天', '8-14天', '15天+']
  const by = new Map(gs.map((g: any) => [g.value, g.low_score_rate]))
  const labels = order.filter(x => by.has(x))
  const values = labels.map(x => by.get(x)!)
  return areaOption(labels, values, '低评分率')
})

function fmtPct(v: number | undefined | null) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

onMounted(async () => {
  try {
    const [a, m] = await Promise.all([runAttribution('对低评分进行归因'), getMeta()])
    attr.value = a
    meta.value = m
    // 延迟率 / 平均评分 / 月度趋势（真实数据）
    const [lr, sc, tr] = await Promise.all([
      runQuery('总体延迟率是多少'),
      runQuery('平均评分是多少'),
      runQuery('各月份低评分率趋势'),
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
.kpi-spark { height: 34px; display: flex; align-items: flex-end; }
.no-spark { font-size: 12px; color: var(--text-3); }
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.chart-card.wide { grid-column: 1 / -1; }
.data-note { margin-top: 16px; font-size: 12px; color: var(--text-3); text-align: center; }
</style>
