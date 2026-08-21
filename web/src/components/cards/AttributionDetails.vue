<template>
  <div class="attribution-details">
    <!-- 一、第一层检验 -->
    <section id="attribution-screening" class="analysis-section">
      <div class="section-heading">
        <div>
          <span class="section-index">01</span>
          <h4>单变量关联筛选</h4>
        </div>
        <span class="section-meta">FDR-BH 校正 + 95% 置信区间</span>
      </div>
      <p class="section-note">
        逐项检验候选变量与{{ targetLabel }}的关联；只有校正后 p&lt;0.05 且效应量置信区间排除无效值的变量，才进入共线性处理。
      </p>
      <el-table :data="featureRows" size="small" max-height="520" class="analysis-table">
        <el-table-column prop="label" label="变量" fixed min-width="130" />
        <el-table-column prop="target" label="检验目标" min-width="100" />
        <el-table-column prop="method" label="检验方法" min-width="230" show-overflow-tooltip />
        <el-table-column prop="pRaw" label="原始 p 值" min-width="105" />
        <el-table-column prop="pAdjusted" label="FDR p 值" min-width="105" />
        <el-table-column prop="effect" label="效应量" min-width="150" />
        <el-table-column prop="ci" label="效应量 95%CI" min-width="150" />
        <el-table-column label="筛选结论" min-width="110" align="center">
          <template #default="{ row }">
            <span class="status-pill" :class="row.statusClass">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="sample" label="有效样本" min-width="100" align="right" />
      </el-table>
      <p class="section-foot">单变量显著不等于业务影响大，也不代表因果；第二层模型用于判断控制已纳入变量后关联是否仍然存在。</p>
    </section>

    <!-- 二、共线性与控制变量 -->
    <section id="attribution-controls" class="analysis-section">
      <div class="section-heading">
        <div>
          <span class="section-index">02</span>
          <h4>共线性代表与固定控制变量</h4>
        </div>
        <span class="section-meta">{{ selectedRows.length }} 个变量进入调整模型</span>
      </div>
      <p class="section-note">信息高度重复的变量组只保留预先定义、业务含义最直观的代表变量，不按本次 p 值或 OR 临时挑选。</p>

      <div v-if="collinearGroups.length" class="collinear-grid">
        <div v-for="group in collinearGroups" :key="group.key" class="collinear-card">
          <div class="collinear-head">
            <b>{{ group.label }}</b>
            <span>{{ group.rows.length }} 项候选</span>
          </div>
          <div class="feature-tags">
            <span v-for="row in group.rows" :key="row.feature" :class="['feature-tag', { selected: row.selected_for_logistic }]">
              {{ row.label }}<small>{{ row.selected_for_logistic ? '入模' : '剔除冗余' }}</small>
            </span>
          </div>
        </div>
      </div>

      <div class="control-grid">
        <div v-if="controlPolicy.order?.length" class="control-card">
          <div class="control-title">订单级模型固定控制</div>
          <div class="control-list"><span v-for="x in controlPolicy.order || []" :key="x">{{ x }}</span></div>
        </div>
        <div v-if="controlPolicy.seller?.length" class="control-card">
          <div class="control-title">订单-卖家级模型固定控制</div>
          <div class="control-list"><span v-for="x in controlPolicy.seller || []" :key="x">{{ x }}</span></div>
        </div>
      </div>
      <p v-if="controlPolicy.selection_rule" class="section-foot">{{ controlPolicy.selection_rule }}</p>
    </section>

    <!-- 三、多变量调整 -->
    <section id="attribution-adjusted" class="analysis-section">
      <div class="section-heading">
        <div>
          <span class="section-index">03</span>
          <h4>多变量 Logistic 调整结果</h4>
        </div>
        <span class="section-meta">{{ stableRows.length }} 项调整后仍显著</span>
      </div>
      <div v-if="modelNotes.length" class="model-notes">
        <div v-for="m in modelNotes" :key="m.label" :class="['model-note', { warn: m.fallback }]">
          <b>{{ m.label }}</b><span>{{ m.method }}</span>
        </div>
      </div>
      <el-table :data="adjustedRows" size="small" max-height="540" class="analysis-table">
        <el-table-column prop="label" label="变量" fixed min-width="135" />
        <el-table-column prop="model" label="调整模型" min-width="175" show-overflow-tooltip />
        <el-table-column prop="method" label="检验方法" min-width="250" show-overflow-tooltip />
        <el-table-column prop="effect" label="调整后效应" min-width="170" />
        <el-table-column prop="ci" label="95%CI" min-width="145" />
        <el-table-column prop="pAdjusted" label="FDR p 值" min-width="105" />
        <el-table-column label="调整后结论" min-width="125" align="center">
          <template #default="{ row }">
            <span class="status-pill" :class="row.statusClass">{{ row.status }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="conclusion" label="解释" min-width="220" show-overflow-tooltip />
      </el-table>
    </section>

    <!-- 四、稳定变量表现 -->
    <section id="attribution-explanations" class="analysis-section">
      <div class="section-heading">
        <div>
          <span class="section-index">04</span>
          <h4>调整后稳定变量如何表现</h4>
        </div>
        <span class="section-meta">分布与重点对象定位</span>
      </div>
      <p class="section-note">以下内容用于回答“关联具体表现在哪里”；展示分布、分位区间或稳定对象，不将统计关联解释为因果。</p>
      <el-collapse v-if="explanations.length" v-model="openExplanations" class="explanation-collapse">
        <el-collapse-item v-for="ex in explanations" :key="ex.feature" :name="ex.feature">
          <template #title>
            <div class="explanation-title">
              <b>{{ ex.label }}</b>
              <span>{{ adjustedEffect(ex.adjusted_result) }}</span>
            </div>
          </template>

          <p class="explanation-copy">{{ ex.interpretation }}</p>
          <template v-if="targetChartOption(ex)">
            <div class="detail-label">特征分组对应的{{ targetLabel }}发生率</div>
            <BaseChart :option="targetChartOption(ex)" :height="targetChartHeight(ex)" />
            <p class="section-foot">{{ ex.target_visualization?.note ?? `柱高表示各分组的${targetLabel}发生率。` }}</p>
          </template>

          <template v-if="isLowScoreTarget && delayChartOption(ex)">
            <div class="detail-label">按是否延迟分层的描述性分布</div>
            <BaseChart :option="delayChartOption(ex)" :height="chartHeight(ex)" />
            <p class="section-foot">{{ ex.delay_visualization?.note }}</p>
          </template>

          <template v-if="ex.feature === 'route' && routeValidationRows(ex).length">
            <div class="detail-label">跨时间保持同方向的线路</div>
            <DataTable :rows="routeValidationRows(ex)" value-key="较早期调整 OR" />
          </template>

          <template v-else-if="ex.kind === 'numeric'">
            <div v-if="numericTargetRows(ex).length" class="detail-label">{{ targetPositiveLabel }}与{{ targetNegativeLabel }}记录的分布</div>
            <DataTable v-if="numericTargetRows(ex).length" :rows="numericTargetRows(ex)" />
            <div v-if="numericBinRows(ex).length" class="detail-label sub">分位区间对应的{{ targetLabel }}发生率</div>
            <DataTable v-if="numericBinRows(ex).length" :rows="numericBinRows(ex)" :value-key="targetRateColumn" />
          </template>

          <template v-else-if="groupDetailRows(ex).length">
            <div class="detail-label">重点分组（显著高风险、超额目标事件与样本量综合排序）</div>
            <DataTable :rows="groupDetailRows(ex)" :value-key="targetRateColumn" />
          </template>

          <template v-if="stableLevelRows(ex).length">
            <div class="detail-label sub">相对参考组仍显著的分类水平</div>
            <DataTable :rows="stableLevelRows(ex)" value-key="调整后 OR" />
          </template>
        </el-collapse-item>
      </el-collapse>
      <div v-else class="empty-block">当前没有变量同时通过调整后显著性与置信区间门槛。</div>
    </section>

    <!-- 五、补充下钻 -->
    <section v-if="isLowScoreTarget" id="attribution-drilldown" class="analysis-section">
      <div class="section-heading">
        <div>
          <span class="section-index">05</span>
          <h4>补充描述性下钻</h4>
        </div>
        <span class="section-meta">优先级 {{ priorityRows.length }} · 线路 {{ routeRows.length }} · 品类 {{ categoryRows.length }}</span>
      </div>
      <p class="section-note">本节用于定位需要排查的业务对象；描述性排名与前两层统计结论不可相互替代。</p>
      <el-collapse v-model="openSupplements" class="supplement-collapse">
        <el-collapse-item v-if="priorityRows.length" title="问题对象排查优先级" name="priority">
          <DataTable :rows="priorityRows" value-key="低评分率" show-rank />
        </el-collapse-item>
        <el-collapse-item v-if="routeRows.length" title="线路描述性下钻" name="route">
          <DataTable :rows="routeRows" value-key="低评分率" show-rank />
        </el-collapse-item>
        <el-collapse-item v-if="categoryRows.length" title="商品品类描述性排名" name="category">
          <DataTable :rows="categoryRows" value-key="低评分率" show-rank />
        </el-collapse-item>
        <el-collapse-item v-if="significantCategoryRows.length" title="经多重校正后显著的高风险品类" name="category-significance">
          <DataTable :rows="significantCategoryRows" value-key="低评分率" show-rank />
        </el-collapse-item>
        <el-collapse-item v-if="significantProductRows.length" title="经多重校正后显著的高风险商品" name="product-significance">
          <DataTable :rows="significantProductRows" value-key="低评分率" show-rank />
        </el-collapse-item>
      </el-collapse>
      <p v-if="itemDrilldown.grain_note" class="section-foot">商品项口径：{{ itemDrilldown.grain_note }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import BaseChart from '../charts/BaseChart.vue'
import DataTable from './DataTable.vue'
import { finiteNumber, fmtNum, fmtP, fmtPct } from '../../format'

const props = defineProps<{ d: any }>()

const featureTests = computed<any[]>(() => props.d?.feature_tests ?? [])
const selectedRows = computed<any[]>(() => props.d?.selected_features ?? [])
const adjustedResults = computed<any[]>(() => props.d?.adjusted_validation?.results ?? [])
const explanations = computed<any[]>(() => props.d?.adjusted_explanations ?? [])
const controlPolicy = computed<any>(() => props.d?.control_policy ?? {})
const itemDrilldown = computed<any>(() => props.d?.item_drilldown ?? {})
const targetLabel = computed(() => props.d?.target_short_label ?? '低评分')
const targetPositiveLabel = computed(() => props.d?.target_positive_label ?? '低评分')
const targetNegativeLabel = computed(() => props.d?.target_negative_label ?? '非低评分')
const targetRateColumn = computed(() => `${targetLabel.value}发生率`)
const isLowScoreTarget = computed(() => (props.d?.target ?? 'is_low_score') === 'is_low_score')
const openExplanations = ref<string[]>([String(explanations.value[0]?.feature ?? '')].filter(Boolean))
const openSupplements = ref<string[]>([])

function fmtInt(value: any): string {
  const n = finiteNumber(value)
  return n == null ? '—' : Math.round(n).toLocaleString('zh-CN')
}

function fmtCi(ci: any, digits = 3): string {
  if (!Array.isArray(ci) || ci.length < 2) return '—'
  return `[${fmtNum(ci[0], digits)}, ${fmtNum(ci[1], digits)}]`
}

function effectText(row: any): string {
  return row?.effect_name && finiteNumber(row?.effect_value) != null
    ? `${row.effect_name}=${fmtNum(row.effect_value, 4)}`
    : '—'
}

const featureRows = computed(() => featureTests.value.map((row: any) => ({
  label: row.label ?? row.feature,
  target: row.target ?? props.d?.target_label ?? '是否低评分',
  method: row.method ?? '—',
  pRaw: fmtP(row.p),
  pAdjusted: fmtP(row.p_adjusted ?? row.p),
  effect: effectText(row),
  ci: fmtCi(row.ci95, 4),
  status: row.assumption_ok === false ? '前提不足' : row.significant && row.ci_passed ? '通过' : '未通过',
  statusClass: row.assumption_ok === false ? 'warn' : row.significant && row.ci_passed ? 'pass' : 'muted',
  sample: fmtInt(row.sample),
})))

const GROUP_LABELS: Record<string, string> = {
  delivery_result: '最终交付结果', delivery_severity: '延迟程度', fulfillment_time: '履约时长',
  approval_time: '支付审批', customer_region: '客户地区', category: '品类',
  payment_channel: '支付渠道', purchase_time: '购买时间', order_value: '订单金额',
  freight_burden: '运费负担', order_complexity: '订单复杂度', seller_complexity: '卖家复杂度',
  shipping_geography: '运输地理', seller_region: '卖家地区', route: '运输线路',
  handover_result: '交接履约', sla_window: '承诺时效',
}
const FEATURE_GROUPS: Record<string, string> = {
  is_late_delivery: 'delivery_result', delay_bucket: 'delivery_result',
  late_days: 'delivery_result', fulfillment_days: 'delivery_result',
  approval_days: 'approval_time', customer_state: 'customer_region',
  primary_category_name: 'category', primary_payment_type: 'payment_channel',
  order_month: 'purchase_time', price_total: 'order_value', freight_ratio: 'freight_burden',
  item_count: 'order_complexity', is_multi_seller_order: 'seller_complexity',
  promised_delivery_days: 'sla_window', seller_price: 'order_value',
  seller_freight_ratio: 'freight_burden', seller_items: 'order_complexity',
  cross_state: 'shipping_geography', distance_km: 'shipping_geography',
  seller_state: 'seller_region', route: 'route', is_any_item_handover_late: 'handover_result',
}

const collinearGroups = computed(() => {
  const source = props.d?.significant_features ?? []
  const groups = new Map<string, any[]>()
  for (const row of source) {
    const key = row.collinear_group || FEATURE_GROUPS[row.feature] || row.feature || 'other'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key)!.push(row)
  }
  return Array.from(groups, ([key, rows]) => ({ key, label: GROUP_LABELS[key] ?? key, rows }))
})

const stableRows = computed(() => adjustedResults.value.filter((row: any) => row.stable))
const adjustedRows = computed(() => adjustedResults.value.map((row: any) => {
  const ok = row.ok !== false
  const hasOr = finiteNumber(row.adjusted_or) != null
  return {
    label: row.label ?? row.feature,
    model: row.model ?? '—',
    method: row.method ?? '—',
    effect: !ok ? '—' : hasOr ? `OR=${fmtNum(row.adjusted_or, 3)}` : '分类变量整体联合检验',
    ci: hasOr ? fmtCi(row.ci95, 3) : (ok ? '分类水平见下一节' : '—'),
    pAdjusted: ok ? fmtP(row.p_adjusted ?? row.p) : '—',
    status: !ok ? '未估计' : row.stable ? '仍显著' : '未通过',
    statusClass: !ok ? 'warn' : row.stable ? 'pass' : 'muted',
    conclusion: !ok ? `未完成：${row.error ?? '模型未稳定估计'}` : row.conclusion,
  }
}))

const modelNotes = computed(() => (props.d?.adjusted_validation?.models ?? []).map((m: any) => ({
  label: m.label ?? '调整模型',
  method: modelMethodText(m),
  fallback: Boolean(m.fallback_reason || m.ok === false),
})))

function modelMethodText(model: any): string {
  if (model.ok === false) return `未稳定估计：${model.error ?? '未知原因'}`
  if (!model.fallback_reason) return model.fit_method ?? 'Logistic'
  const reason = String(model.fallback_reason)
  if (/singular matrix/i.test(reason)) {
    return 'Newton 求解矩阵奇异；已改用 Binomial GLM（IRLS），结果已收敛'
  }
  if (/未收敛|converg/i.test(reason)) {
    return 'Newton 求解未收敛；已改用 Binomial GLM（IRLS），结果已收敛'
  }
  return `Newton 求解失败（${reason}）；已改用 Binomial GLM（IRLS）`
}

function adjustedEffect(result: any): string {
  if (!result) return '—'
  if (finiteNumber(result.adjusted_or) != null) {
    return `调整后 OR ${fmtNum(result.adjusted_or, 3)} · 95%CI ${fmtCi(result.ci95, 3)}`
  }
  if (result.feature === 'route') return `稳定线路 ${(result.stable_routes ?? []).length} 条`
  return `联合检验 FDR p=${fmtP(result.p_adjusted ?? result.p)}`
}

function binaryLabel(feature: string, value: any): string {
  const labels: Record<string, Record<string, string>> = {
    is_late_delivery: { '0': '未延迟', '1': '延迟' },
    cross_state: { '0': '同州', '1': '跨州' },
    is_multi_seller_order: { '0': '单卖家订单', '1': '多卖家订单' },
    is_any_item_handover_late: { '0': '无交接超期', '1': '存在交接超期' },
  }
  return labels[feature]?.[String(value)] ?? String(value ?? '—')
}

function normalizedTargetRows(ex: any): any[] {
  const supplied = ex?.target_visualization?.rows
  if (Array.isArray(supplied) && supplied.length) return supplied
  if (ex?.kind === 'numeric') {
    return (ex?.details?.quantile_bins ?? []).map((row: any) => ({
      group: row.value_range,
      sample: row.sample,
      target_count: row.target_count ?? row.low_score_count,
      target_rate: row.target_rate ?? row.low_score_rate,
    }))
  }
  if (!Array.isArray(ex?.details)) return []
  return ex.details.map((row: any) => ({
    group: row.value,
    sample: row.sample,
    target_count: row.target_count ?? row.low_score_count,
    target_rate: row.target_rate ?? row.low_score_rate,
  }))
}

function targetChartOption(ex: any): any | null {
  const rows = normalizedTargetRows(ex)
    .filter((row: any) => finiteNumber(row.target_rate) != null)
  if (!rows.length) return null
  if (ex?.kind === 'numeric') {
    const shown = rows.slice(0, 10)
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 52, right: 24, top: 24, bottom: 66, containLabel: true },
      xAxis: { type: 'category', data: shown.map((row: any) => String(row.group)), axisLabel: { rotate: shown.length > 4 ? 25 : 0, hideOverlap: true } },
      yAxis: { type: 'value', name: targetRateColumn.value, axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#EEF2F7' } } },
      series: [{
        type: 'bar', barMaxWidth: 46,
        data: shown.map((row: any) => +(((finiteNumber(row.target_rate) ?? 0) * 100).toFixed(2))),
        itemStyle: { color: '#4F7BF7', borderRadius: [5, 5, 0, 0] },
        label: { show: true, position: 'top', formatter: '{c}%' },
      }],
    }
  }
  const shown = rows.slice().sort((a: any, b: any) =>
    (finiteNumber(b.target_rate) ?? 0) - (finiteNumber(a.target_rate) ?? 0),
  ).slice(0, 15).reverse()
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 18, right: 62, top: 16, bottom: 24, containLabel: true },
    xAxis: { type: 'value', name: targetRateColumn.value, axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#EEF2F7' } } },
    yAxis: { type: 'category', data: shown.map((row: any) => binaryLabel(ex.feature, row.group)), axisLabel: { width: 150, overflow: 'truncate' } },
    series: [{
      type: 'bar', barMaxWidth: 24,
      data: shown.map((row: any) => +(((finiteNumber(row.target_rate) ?? 0) * 100).toFixed(2))),
      itemStyle: { color: '#4F7BF7', borderRadius: [0, 5, 5, 0] },
      label: { show: true, position: 'right', formatter: '{c}%' },
    }],
  }
}

function targetChartHeight(ex: any): string {
  if (ex?.kind === 'numeric') return '300px'
  return `${Math.max(260, Math.min(500, normalizedTargetRows(ex).length * 30 + 70))}px`
}

function delayChartOption(ex: any): any | null {
  const visual = ex?.delay_visualization
  const rows = visual?.rows ?? []
  if (!visual?.ok || !rows.length) return null
  if (visual.chart_type === 'delay_outcome') {
    const outcomeRows = rows.slice().sort((a: any, b: any) =>
      Number(a.delay_status === '延迟') - Number(b.delay_status === '延迟'),
    )
    const labels = outcomeRows.map((row: any) => binaryLabel(ex.feature, row.group))
    const values = outcomeRows.map((row: any) => ({
      value: +(((finiteNumber(row.low_score_rate) ?? 0) * 100).toFixed(1)),
      itemStyle: { color: row.delay_status === '延迟' ? '#F05B66' : '#2F65F6', borderRadius: [6, 6, 0, 0] },
    }))
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 48, right: 24, top: 24, bottom: 38 },
      xAxis: { type: 'category', data: labels, axisTick: { show: false } },
      yAxis: { type: 'value', name: '低评分率', axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#EEF2F7' } } },
      series: [{ type: 'bar', barWidth: 44, data: values, label: { show: true, position: 'top', formatter: '{c}%' } }],
    }
  }
  const order = (visual.group_order?.length ? visual.group_order : [...new Set(rows.map((r: any) => r.group))]).map(String)
  const statuses = ['未延迟', '延迟']
  const color = ['#2F65F6', '#F05B66']
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: statuses, top: 0, right: 8 },
    grid: { left: 48, right: 24, top: 42, bottom: order.length > 8 ? 88 : 58 },
    xAxis: { type: 'category', data: order.map((x: string) => binaryLabel(ex.feature, x)), axisLabel: { rotate: order.length > 6 ? 35 : 0, hideOverlap: true } },
    yAxis: { type: 'value', name: '组内占比', axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#EEF2F7' } } },
    series: statuses.map((status, index) => ({
      name: status, type: 'bar', barMaxWidth: 34,
      data: order.map((group: string) => {
        const row = rows.find((r: any) => String(r.group) === group && r.delay_status === status)
        return +(((finiteNumber(row?.within_delay_share) ?? 0) * 100).toFixed(2))
      }),
      itemStyle: { color: color[index], borderRadius: [4, 4, 0, 0] },
    })),
  }
}

function chartHeight(ex: any): string {
  const count = ex?.delay_visualization?.group_order?.length ?? 0
  return count > 10 ? '360px' : '300px'
}

function numericTargetRows(ex: any): any[] {
  return (ex?.details?.by_target ?? []).map((row: any) => ({
    '分组': row.group, '样本量': fmtInt(row.sample), 'P25': fmtNum(row.p25, 3),
    '中位数': fmtNum(row.median, 3), 'P75': fmtNum(row.p75, 3), '均值': fmtNum(row.mean, 3),
  }))
}

function numericBinRows(ex: any): any[] {
  return (ex?.details?.quantile_bins ?? []).map((row: any) => ({
    '变量区间': row.value_range, '样本量': fmtInt(row.sample),
    [`${targetLabel.value}记录数`]: fmtInt(row.target_count ?? row.low_score_count),
    [targetRateColumn.value]: fmtPct(row.target_rate ?? row.low_score_rate),
  }))
}

function groupDetailRows(ex: any): any[] {
  if (!Array.isArray(ex?.details)) return []
  return ex.details.slice().sort((a: any, b: any) =>
    Number(Boolean(b.significant_risk)) - Number(Boolean(a.significant_risk))
    || (finiteNumber(b.excess_target ?? b.excess_low_score) ?? 0) - (finiteNumber(a.excess_target ?? a.excess_low_score) ?? 0)
    || (finiteNumber(b.sample) ?? 0) - (finiteNumber(a.sample) ?? 0),
  ).slice(0, 20).map((row: any) => ({
    '对象/分组': binaryLabel(ex.feature, row.value), '样本量': fmtInt(row.sample),
    [targetRateColumn.value]: fmtPct(row.target_rate ?? row.low_score_rate), '高于总体': row.rate_difference == null ? '—' : `${row.rate_difference >= 0 ? '+' : ''}${fmtPct(row.rate_difference)}`,
    'Lift': fmtNum(row.lift, 3), [`超额${targetLabel.value}记录`]: fmtNum(row.excess_target ?? row.excess_low_score, 1),
    '对象 OR': fmtNum(row.or, 3), 'OR 95%CI': fmtCi(row.ci95, 3), 'FDR p 值': fmtP(row.p_adjusted ?? row.p),
  }))
}

function stableLevelRows(ex: any): any[] {
  return (ex?.adjusted_result?.level_results ?? []).filter((row: any) => row.stable_level)
    .sort((a: any, b: any) => (finiteNumber(b.adjusted_or) ?? 0) - (finiteNumber(a.adjusted_or) ?? 0))
    .slice(0, 20).map((row: any) => ({
      '类别（相对参考组）': row.level, '调整后 OR': fmtNum(row.adjusted_or, 3),
      '95%CI': fmtCi(row.ci95, 3), 'FDR p 值': fmtP(row.p_adjusted ?? row.p),
    }))
}

function routeValidationRows(ex: any): any[] {
  return (ex?.route_validation ?? []).map((row: any) => ({
    '线路': row.route, '较早期样本': fmtInt(row.train_n), [`较早期${targetRateColumn.value}`]: fmtPct(row.train_target_rate ?? row.train_low_score_rate),
    '较早期调整 OR': fmtNum(row.adjusted_or, 3), '调整 OR 95%CI': fmtCi(row.adjusted_ci95, 3),
    'FDR p 值': fmtP(row.adjusted_p_fdr), '较晚期样本': fmtInt(row.holdout_n),
    [`较晚期${targetRateColumn.value}`]: fmtPct(row.holdout_target_rate ?? row.holdout_low_score_rate), '较晚期 OR': fmtNum(row.holdout_or, 3),
    '稳定性': row.stability ?? '—',
  }))
}

const DIM_LABELS: Record<string, string> = {
  is_late_delivery: '是否延迟', delay_bucket: '延迟分档', customer_state: '客户州',
  primary_category_name: '主要品类', primary_payment_type: '支付方式', order_month: '购买月份',
  seller_state: '卖家州', route: '线路', cross_state: '是否跨州', is_multi_seller_order: '是否多卖家订单',
}
const priorityRows = computed(() => (props.d?.priorities ?? []).map((row: any) => ({
  '优先级': row.priority, '维度': DIM_LABELS[row.dimension] ?? row.dimension,
  '对象': binaryLabel(row.dimension, row.value), '样本量': fmtInt(row.sample),
  '低评分率': fmtPct(row.low_score_rate), 'Lift': fmtNum(row.lift, 2), '超额低评分': fmtNum(row.excess_low_score, 1),
})))

const routeRows = computed(() => {
  const raw = props.d?.routes
  const blocks = Array.isArray(raw) ? raw : raw ? [raw] : []
  return blocks.flatMap((block: any) => block?.top_routes ?? []).map((row: any) => ({
    '优先级': row.priority, '线路': row.value, '样本量': fmtInt(row.sample),
    '低评分率': fmtPct(row.low_score_rate), 'Lift': fmtNum(row.lift, 2), '超额低评分': fmtNum(row.excess_low_score, 1),
  }))
})

const categoryRows = computed(() => (itemDrilldown.value?.by_category ?? []).map((row: any) => ({
  '品类': row.category_name, '订单数': fmtInt(row._m_distinct_orders),
  '低评分数': fmtInt(row._m_low_score_orders), '低评分率': fmtPct(row._m_low_score_rate),
  '商品金额': fmtNum(row._m_item_product_value, 2), '运费金额': fmtNum(row._m_item_freight_value, 2),
})))

function significanceRows(type: 'category' | 'product'): any[] {
  const rows = itemDrilldown.value?.significance?.[type]?.significant_risk ?? []
  return rows.slice(0, 20).map((row: any) => ({
    [type === 'category' ? '品类' : '商品 ID']: row.value,
    '订单数': fmtInt(row.sample), '低评分率': fmtPct(row.low_score_rate), 'Lift': fmtNum(row.lift, 2),
    'OR': fmtNum(row.or, 3), 'OR 95%CI': fmtCi(row.or_ci, 3), 'FDR p 值': fmtP(row.p_adjusted ?? row.p),
  }))
}
const significantCategoryRows = computed(() => significanceRows('category'))
const significantProductRows = computed(() => significanceRows('product'))
</script>

<style scoped>
.attribution-details { display: flex; flex-direction: column; gap: 14px; margin-top: 14px; }
.analysis-section { border: 1px solid var(--border-soft); border-radius: var(--radius-md); background: #fff; padding: 16px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 8px; }
.section-heading > div { display: flex; align-items: center; gap: 10px; }
.section-index { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 26px; border-radius: 8px; background: #EFF6FF; color: var(--primary); font-size: 11px; font-weight: 800; }
.section-heading h4 { margin: 0; color: var(--text-1); font-size: 15px; font-weight: 700; }
.section-meta { color: var(--text-3); font-size: 12px; white-space: nowrap; }
.section-note, .section-foot, .explanation-copy { margin: 0 0 12px; color: var(--text-3); font-size: 12px; line-height: 1.65; }
.section-foot { margin: 9px 0 0; }
.analysis-table { width: 100%; border: 1px solid #EEF2F7; border-radius: 8px; overflow: hidden; }
.status-pill { display: inline-flex; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 650; }
.status-pill.pass { color: #047857; background: #ECFDF5; }
.status-pill.warn { color: #B45309; background: #FFFBEB; }
.status-pill.muted { color: #64748B; background: #F1F5F9; }
.collinear-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; margin-bottom: 14px; }
.collinear-card { border: 1px solid #EEF2F7; border-radius: 10px; padding: 10px 12px; background: #FBFDFF; }
.collinear-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.collinear-head b { font-size: 12px; color: var(--text-1); }
.collinear-head span { font-size: 10px; color: var(--text-3); }
.feature-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.feature-tag { display: inline-flex; align-items: center; gap: 5px; border-radius: 7px; padding: 4px 7px; background: #F1F5F9; color: #64748B; font-size: 11px; }
.feature-tag.selected { background: #EAF2FF; color: var(--primary); font-weight: 650; }
.feature-tag small { font-size: 9px; opacity: .78; }
.control-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.control-card { background: #F8FAFC; border-radius: 10px; padding: 12px; }
.control-title { color: var(--text-2); font-size: 12px; font-weight: 700; margin-bottom: 8px; }
.control-list { display: flex; flex-wrap: wrap; gap: 6px; }
.control-list span { border: 1px solid #E2E8F0; border-radius: 7px; background: #fff; padding: 4px 7px; color: var(--text-2); font-size: 10px; }
.model-notes { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.model-note { display: flex; gap: 7px; align-items: center; background: #F0FDF4; color: #166534; border-radius: 8px; padding: 7px 10px; font-size: 11px; }
.model-note.warn { background: #FFFBEB; color: #92400E; }
.model-note b { font-weight: 700; }
.explanation-collapse, .supplement-collapse { border-top: none; border-bottom: none; }
.explanation-title { display: flex; align-items: center; gap: 12px; min-width: 0; }
.explanation-title b { color: var(--text-1); font-size: 13px; }
.explanation-title span { color: var(--text-3); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail-label { margin: 14px 0 6px; color: var(--text-2); font-size: 12px; font-weight: 700; }
.detail-label.sub { margin-top: 16px; }
.empty-block { border: 1px dashed #CBD5E1; border-radius: 8px; padding: 14px; color: var(--text-3); font-size: 12px; text-align: center; }
:deep(.el-table th.el-table__cell) { background: #F8FAFC; color: var(--text-2); font-size: 11px; font-weight: 700; }
:deep(.el-table td.el-table__cell) { color: var(--text-2); font-size: 11px; }
:deep(.el-collapse-item__header) { color: var(--text-2); font-size: 12px; font-weight: 650; }
:deep(.el-collapse-item__content) { padding: 0 4px 14px; }
@media (max-width: 1100px) { .collinear-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 760px) {
  .analysis-section { padding: 12px; }
  .section-heading { align-items: flex-start; }
  .section-meta { white-space: normal; text-align: right; }
  .collinear-grid, .control-grid { grid-template-columns: 1fr; }
}
</style>
