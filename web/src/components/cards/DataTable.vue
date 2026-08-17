<template>
  <div class="data-table">
    <el-table :data="shown" size="small" :max-height="expanded ? 420 : 300">
      <!-- 排名徽章 -->
      <el-table-column v-if="showRank" label="#" width="48" align="center">
        <template #default="{ $index }">
          <span class="rank" :class="`r${$index + 1}`">{{ $index + 1 }}</span>
        </template>
      </el-table-column>
      <!-- 动态列 -->
      <el-table-column
        v-for="k in keys" :key="k"
        :prop="k" :label="k"
        :min-width="k === valueKey ? 150 : 110"
      >
        <template #default="{ row }">
          <template v-if="k === valueKey && num(row[k]) != null">
            <div class="cell-bar">
              <span class="bar" :style="{ width: pct(row[k]) }"></span>
              <span class="cell-val">{{ row[k] }}</span>
            </div>
          </template>
          <template v-else>{{ row[k] }}</template>
        </template>
      </el-table-column>
    </el-table>
    <!-- 长数据折叠 -->
    <div v-if="rows.length > LIMIT" class="expand" @click="expanded = !expanded">
      {{ expanded ? '收起 ⌃' : `展开全部 ${rows.length} 组数据 ⌄` }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  rows: any[]
  valueKey?: string
  showRank?: boolean
}>()

const LIMIT = 6
const expanded = ref(false)

const keys = computed(() => (props.rows[0] ? Object.keys(props.rows[0]) : []))
const shown = computed(() => (expanded.value ? props.rows : props.rows.slice(0, LIMIT)))

// 解析单元格里的数字（"18.89%" → 18.89）
function num(v: any): number | null {
  if (v == null) return null
  const n = parseFloat(String(v).replace('%', '').replace(',', ''))
  return isNaN(n) ? null : n
}
function pct(v: any): string {
  const n = num(v)
  if (n == null) return '0%'
  const max = Math.max(...props.rows.map(r => num(r[props.valueKey!]) ?? 0), 1)
  return `${Math.max(2, Math.min(100, (n / max) * 100))}%`
}
</script>

<style scoped>
.data-table { margin-top: 8px; }
.rank {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 8px;
  font-size: 11px; font-weight: 700;
}
.rank.r1 { background: #2F65F6; color: #fff; }
.rank.r2 { background: #38BDF8; color: #fff; }
.rank.r3 { background: #93C5FD; color: #fff; }
.rank:not(.r1):not(.r2):not(.r3) { background: #F1F5F9; color: #64748B; }
.cell-bar { position: relative; height: 22px; display: flex; align-items: center; }
.cell-bar .bar {
  position: absolute; left: 0; top: 3px; bottom: 3px;
  background: linear-gradient(90deg, rgba(47,101,246,.12), rgba(56,189,248,.10));
  border-radius: 6px;
}
.cell-bar .cell-val { position: relative; z-index: 1; font-weight: 600; color: var(--text-1); }
.expand {
  margin-top: 8px; text-align: center; cursor: pointer;
  font-size: 12px; color: var(--primary); padding: 6px;
  border-radius: var(--radius-sm); transition: background .15s ease;
}
.expand:hover { background: #EFF6FF; }
:deep(.el-table th.el-table__cell) {
  background: #F8FAFC;
  font-weight: 600; color: var(--text-2);
}
</style>
