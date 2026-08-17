<template>
  <div class="result-card">
    <div v-if="renderError" class="rc-error">⚠ 结果渲染出错：{{ renderError }}</div>
    <!-- 归因结果 -->
    <template v-if="intent === 'attribution'">
      <div class="rc-row">
        <div class="rc-metric">
          <span>订单级低评分率</span>
          <b>{{ fmtPct(d.baseline?.order?.low_score_rate) }}</b>
        </div>
        <div class="rc-metric">
          <span>卖家级低评分率</span>
          <b>{{ fmtPct(d.baseline?.seller?.low_score_rate) }}</b>
        </div>
        <div class="rc-metric">
          <span>延迟 OR</span>
          <b>{{ fmtNum(d.verification?.evidence?.or) }}</b>
        </div>
        <div class="rc-metric">
          <span>证据分级</span>
          <b>{{ d.verification?.evidence?.grade ?? '—' }}</b>
        </div>
      </div>

      <h4 class="rc-title">优先级问题对象</h4>
      <el-table :data="d.priorities ?? []" size="small" max-height="300">
        <el-table-column prop="priority" label="优先级" width="80" />
        <el-table-column prop="dimension" label="维度" width="150" />
        <el-table-column prop="value" label="对象" width="140" />
        <el-table-column prop="sample" label="样本" width="80" />
        <el-table-column label="低评分率">
          <template #default="{ row }">{{ fmtPct(row.rate) }}</template>
        </el-table-column>
        <el-table-column prop="lift" label="Lift" width="80">
          <template #default="{ row }">{{ row.lift?.toFixed?.(2) }}</template>
        </el-table-column>
        <el-table-column prop="excess_low_score" label="超额低评分" width="90" />
      </el-table>

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

      <div v-if="d.caveats?.length" class="rc-caveat">
        {{ d.caveats.join('；') }}
      </div>
    </template>

    <!-- 统计结果 -->
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
      <div v-if="d.top_groups?.length">
        <h4 class="rc-title">分组详情</h4>
        <el-table :data="d.top_groups" size="small" max-height="260">
          <el-table-column prop="value" label="分组" />
          <el-table-column prop="n" label="样本" width="90" />
          <el-table-column label="比例">
            <template #default="{ row }">{{ fmtPct(row.rate ?? row.prop ?? row.ratio) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </template>

    <!-- 查询结果 -->
    <template v-else-if="intent === 'query'">
      <div class="rc-row">
        <div class="rc-metric"><span>模式</span><b>{{ d.execution_mode ?? '确定' }}</b></div>
        <div class="rc-metric"><span>数据行</span><b>{{ d.row_count ?? 0 }}</b></div>
        <div class="rc-metric"><span>表</span><b>{{ d.table ?? '—' }}</b></div>
      </div>
      <p v-if="d.answer" class="rc-conclusion">{{ d.answer }}</p>
      <div v-if="d.rows?.length">
        <el-table :data="d.rows" size="small" max-height="300" border>
          <el-table-column v-for="k in Object.keys(d.rows[0])" :key="k" :prop="k" :label="k" min-width="110" />
        </el-table>
      </div>
      <div v-if="d.sql" class="rc-sql">SQL：{{ d.sql }}</div>
    </template>

    <div v-else class="rc-caveat">（暂无结构化渲染，请查看上方文本回答）</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onErrorCaptured, ref } from 'vue'
import BaseChart from '../charts/BaseChart.vue'
import { forestOption } from '../../charts'
import { fmtNum, fmtP } from '../../format'

const props = defineProps<{ intent: string; d: any }>()

// 子组件渲染错误局部显示，避免整页白屏
const renderError = ref('')
onErrorCaptured((err) => {
  renderError.value = String((err as any)?.message ?? err)
  return false
})

function fmtPct(v: number | undefined) {
  return v == null ? '—' : (v * 100).toFixed(1) + '%'
}

const terms = computed(() => {
  const lo = props.d?.verification?.logistic?.order?.terms ?? []
  return lo.filter((t: any) => !t.term.startsWith('C(') || t.term.includes('is_late_delivery'))
})

const forest = computed(() =>
  forestOption(terms.value.slice(0, 8).map((t: any) => ({ ...t, term: shortTerm(t.term) }))),
)

const recs = computed(() => props.d?.recommendations?.recommendations ?? [])

function shortTerm(t: string) {
  if (t.includes('is_late_delivery')) return 'is_late_delivery'
  if (t.startsWith('C(')) return t.replace(/^C\((.*?)\)\[T\.(.*?)\]$/, '$1=$2').slice(0, 26)
  return t.slice(0, 26)
}
</script>

<style scoped>
.result-card { margin-top: 8px; }
.rc-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.rc-metric {
  background: var(--bg); border-radius: var(--radius-md); padding: 12px 14px;
  display: flex; flex-direction: column; gap: 4px;
}
.rc-metric span { font-size: 12px; color: var(--text-3); }
.rc-metric b { font-size: 18px; font-weight: 700; color: var(--text-1); }
.rc-title { font-size: 14px; font-weight: 600; margin: 14px 0 10px; color: var(--text-2); }
.rc-conclusion { color: var(--text-1); font-size: 14px; line-height: 1.7; }
.rc-error { background: var(--red-bg); color: var(--red); padding: 8px 12px; border-radius: var(--radius-sm); font-size: 12px; margin-bottom: 10px; }
.callout {
  display: flex; flex-direction: column; gap: 4px;
  background: #EFF6FF; border-left: 3px solid var(--primary);
  border-radius: var(--radius-md); padding: 12px 14px; margin-bottom: 10px;
}
.callout-label { font-size: 12px; font-weight: 700; color: var(--primary); }
.callout-text { font-size: 13px; color: var(--text-1); line-height: 1.7; }
.rc-caveat { color: var(--text-3); font-size: 12px; margin-top: 12px; line-height: 1.6; }
.rc-sql {
  margin-top: 10px; font-size: 12px; color: var(--text-3);
  background: var(--bg); border-radius: var(--radius-sm); padding: 10px 12px;
  word-break: break-all; font-family: 'SF Mono', Consolas, monospace;
}
.rec {
  border: 1px solid var(--border-soft); border-radius: var(--radius-md);
  padding: 12px 14px; margin-bottom: 10px;
}
.rec-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.rec-resp { color: var(--primary); font-size: 12px; margin-left: auto; }
.rec-body { font-size: 13px; color: var(--text-2); line-height: 1.8; }
</style>
