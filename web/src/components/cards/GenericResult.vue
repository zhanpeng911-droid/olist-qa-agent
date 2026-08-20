<template>
  <div class="gr">
    <template v-if="kind === 'empty'"></template>

    <template v-else-if="kind === 'bool'">
      <span class="gr-pill" :class="value ? 'yes' : 'no'">{{ value ? '是' : '否' }}</span>
    </template>

    <template v-else-if="kind === 'num'">
      <span class="gr-num">{{ numText }}</span>
    </template>

    <template v-else-if="kind === 'str'">
      <span class="gr-str">{{ value }}</span>
    </template>

    <template v-else-if="kind === 'table'">
      <table class="gr-table">
        <thead>
          <tr><th v-for="c in cols" :key="c">{{ keyLabel(c) }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, i) in value" :key="i">
            <td v-for="c in cols" :key="c"><GenericResult :value="row[c]" :depth="depth + 1" /></td>
          </tr>
        </tbody>
      </table>
    </template>

    <template v-else-if="kind === 'tags'">
      <div class="gr-tags">
        <span v-for="(v, i) in value" :key="i" class="gr-tag">{{ String(v) }}</span>
      </div>
    </template>

    <template v-else-if="kind === 'kv'">
      <div class="gr-obj">
        <div v-for="(v, k) in entries" :key="k" class="gr-kv">
          <div class="gr-key">{{ keyLabel(String(k)) }}</div>
          <div class="gr-val"><GenericResult :value="v" :depth="depth + 1" /></div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: any; depth?: number }>()
const depth = computed(() => props.depth ?? 0)

// 常见字段的中文映射，提升通用兜底的可读性；未命中的原样展示
const KEY_LABELS: Record<string, string> = {
  label: '名称', value: '值', feature: '变量', method: '方法', model: '模型',
  p: 'p 值', p_adjusted: '校正 p', or: 'OR', adjusted_or: '调整后 OR',
  ci95: '95%CI', n: '样本', sample: '样本', significant: '显著',
  ok: '成功', error: '错误', effect: '效应', effect_name: '效应量',
  effect_value: '效应值', target: '目标', target_label: '目标', table: '表',
  count: '数量', rate: '率', status: '状态', conclusion: '结论',
  requested_features: '验证变量', summary: '汇总', models: '模型',
  feature_results: '验证结果', requested_labels: '变量名',
}
function keyLabel(k: string): string {
  return KEY_LABELS[k] ?? k
}

const kind = computed(() => {
  const v = props.value
  if (v === null || v === undefined) return 'empty'
  if (typeof v === 'boolean') return 'bool'
  if (typeof v === 'number') return 'num'
  if (typeof v === 'string') return 'str'
  if (Array.isArray(v)) {
    if (!v.length) return 'empty'
    if (v.every(x => x !== null && typeof x === 'object' && !Array.isArray(x))) return 'table'
    return 'tags'
  }
  if (typeof v === 'object') return 'kv'
  return 'str'
})

const numText = computed(() => {
  const v = props.value as number
  if (Number.isInteger(v)) return String(v)
  if (Math.abs(v) < 0.0001 && v !== 0) return v.toExponential(2)
  return String(Number(v.toFixed(4)))
})

const cols = computed(() => Object.keys(props.value[0] ?? {}))

const entries = computed(() => Object.entries(props.value as Record<string, any>))
</script>

<style scoped>
.gr-str { color: var(--text-1); font-size: 13px; line-height: 1.7; word-break: break-word; }
.gr-num { color: var(--text-1); font-size: 13px; font-variant-numeric: tabular-nums; }
.gr-pill { display: inline-block; font-size: 12px; padding: 2px 9px; border-radius: var(--radius-pill); }
.gr-pill.yes { color: #166534; background: #DCFCE7; }
.gr-pill.no { color: #9A3412; background: #FFEDD5; }
.gr-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.gr-tag { font-size: 12px; color: var(--primary); background: #EFF6FF; padding: 3px 10px; border-radius: var(--radius-pill); }
.gr-obj { display: flex; flex-direction: column; gap: 8px; }
.gr-kv { display: flex; gap: 12px; align-items: flex-start; }
.gr-key { flex-shrink: 0; min-width: 76px; font-size: 12px; color: var(--text-3); padding-top: 2px; }
.gr-val { flex: 1; min-width: 0; }
.gr-table { border-collapse: collapse; width: 100%; font-size: 12px; }
.gr-table th, .gr-table td { border: 1px solid var(--border-soft); padding: 6px 10px; text-align: left; }
.gr-table th { background: #F8FAFC; color: var(--text-2); font-weight: 600; white-space: nowrap; }
.gr-table td { color: var(--text-1); }
</style>
