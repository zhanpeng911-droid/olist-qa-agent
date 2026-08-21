<template>
  <div ref="el" :style="{ width: '100%', height: height }"></div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ option: any; height?: string }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null
let frame = 0

/**
 * 折叠面板关闭时容器宽度可能为 0。此时初始化 ECharts 会把横轴压成一条短线。
 * 等容器真正可见后再初始化，并持续监听尺寸变化。
 */
function scheduleRender() {
  if (frame) cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => {
    const target = el.value
    if (!target || target.clientWidth < 10 || target.clientHeight < 10) return
    if (!chart) chart = echarts.init(target)
    chart.setOption(props.option, true)
    chart.resize()
  })
}

onMounted(() => {
  observer = new ResizeObserver(scheduleRender)
  if (el.value) observer.observe(el.value)
  window.addEventListener('resize', scheduleRender)
  nextTick(scheduleRender)
})

watch(() => props.option, scheduleRender, { deep: true })
onBeforeUnmount(() => {
  window.removeEventListener('resize', scheduleRender)
  observer?.disconnect()
  observer = null
  if (frame) cancelAnimationFrame(frame)
  chart?.dispose()
  chart = null
})
</script>
