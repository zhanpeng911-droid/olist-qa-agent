import * as echarts from 'echarts'

/** 平滑面积图（青蓝渐变 → 透明） */
export function areaOption(x: string[], y: number[], name = ''): any {
  return {
    tooltip: { trigger: 'axis', backgroundColor: '#1E293B', textStyle: { color: '#fff' } },
    grid: { left: 44, right: 20, top: 20, bottom: 28 },
    xAxis: { type: 'category', data: x, axisLine: { lineStyle: { color: '#E2E8F0' } }, axisLabel: { color: '#94A3B8' } },
    yAxis: { type: 'value', axisLabel: { color: '#94A3B8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
    series: [{
      name, type: 'line', smooth: true, symbol: 'circle', symbolSize: 7,
      data: y.map(v => +(v * 100).toFixed(1)),
      lineStyle: { width: 3, color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#00C5FF' }, { offset: 1, color: '#2F65F6' }]) },
      itemStyle: { color: '#2F65F6' },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(47,101,246,.28)' },
          { offset: 1, color: 'rgba(0,197,255,0)' },
        ]),
      },
    }],
  }
}

/** 横向条形图（排名），右侧带百分比标签 */
export function barOption(labels: string[], values: number[], name = ''): any {
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#1E293B', textStyle: { color: '#fff' } },
    grid: { left: 90, right: 52, top: 12, bottom: 24 },
    xAxis: { type: 'value', axisLabel: { color: '#94A3B8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
    yAxis: { type: 'category', data: labels, axisLabel: { color: '#64748B' }, axisLine: { show: false }, axisTick: { show: false } },
    series: [{
      name, type: 'bar', barWidth: 16, data: values.map(v => +(v * 100).toFixed(1)),
      label: { show: true, position: 'right', color: '#64748B', fontSize: 12, fontWeight: 600, formatter: '{c}%' },
      itemStyle: {
        borderRadius: [0, 8, 8, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#38BDF8' }, { offset: 1, color: '#2F65F6' }]),
      },
    }],
  }
}

/** 环形图：中心核心数字 + 业务中文图例 */
export function donutOption(labels: string[], values: number[], centerText?: string): any {
  return {
    tooltip: { trigger: 'item', backgroundColor: '#1E293B', textStyle: { color: '#fff' }, formatter: '{b}: {c}%' },
    legend: { bottom: 0, textStyle: { color: '#64748B', fontSize: 12 }, itemWidth: 10, itemHeight: 10 },
    graphic: centerText
      ? [
          { type: 'text', left: 'center', top: '38%', style: { text: centerText, textAlign: 'center', fill: '#0F172A', fontSize: 26, fontWeight: 700 } },
          { type: 'text', left: 'center', top: '52%', style: { text: '有效样本', textAlign: 'center', fill: '#94A3B8', fontSize: 11 } },
        ]
      : [],
    series: [{
      type: 'pie', radius: ['58%', '78%'], center: ['50%', '45%'],
      avoidLabelOverlap: true, itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 3 },
      label: { show: false }, emphasis: { label: { show: true, fontWeight: 700 } },
      data: labels.map((n, i) => ({ name: n, value: values[i] })),
      color: ['#2F65F6', '#38BDF8', '#00C5FF', '#10B981', '#F59E0B', '#F43F5E', '#8B5CF6'],
    }],
  }
}

/** OR 森林图：横线 + 置信区间 */
export function forestOption(terms: any[], title = '调整后 OR (95% CI)'): any {
  const names = terms.map(t => t.term)
  const ors = terms.map(t => t.or)
  const lo = terms.map(t => t.ci95?.[0] ?? t.or)
  const hi = terms.map(t => t.ci95?.[1] ?? t.or)
  return {
    tooltip: {
      formatter: (p: any) => {
        const t = terms[p.dataIndex]
        return `<b>${t.term}</b><br/>OR=${t.or} (95%CI ${t.ci95?.[0]}–${t.ci95?.[1]})<br/>p=${t.p?.toExponential?.(2) ?? t.p}`
      },
      backgroundColor: '#1E293B', textStyle: { color: '#fff' },
    },
    grid: { left: 150, right: 40, top: 24, bottom: 24 },
    xAxis: {
      type: 'value', name: title, axisLabel: { color: '#94A3B8' },
      splitLine: { lineStyle: { color: '#F1F5F9' } },
    },
    yAxis: { type: 'category', data: names, axisLabel: { color: '#64748B' }, axisLine: { show: false }, axisTick: { show: false } },
    series: [
      {
        type: 'bar', data: lo.map((v, i) => [v, hi[i] - v]),
        barWidth: 3, itemStyle: { color: '#2F65F6' }, z: 3,
      },
      {
        type: 'scatter', data: ors, symbolSize: 11,
        itemStyle: { color: '#00C5FF', borderColor: '#fff', borderWidth: 2 },
      },
    ],
  }
}

/** 迷你走势线（KPI sparkline）：简单折线 + 渐变面积，避免平滑曲线起点异常 */
export function sparklineOption(values: number[], color = '#2F65F6'): any {
  return {
    grid: { left: 2, right: 2, top: 6, bottom: 2 },
    xAxis: { type: 'category', show: false, boundaryGap: false, data: values.map((_, i) => i) },
    yAxis: { type: 'value', show: false, scale: true },
    series: [{
      type: 'line', smooth: false, symbol: 'none', data: values,
      lineStyle: { width: 2, color }, areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(47,101,246,.16)' }, { offset: 1, color: 'rgba(47,101,246,0)' },
        ]),
      },
    }],
  }
}

/** 无数据走势：水平淡灰虚线基准线（保持 KPI 卡片视觉体量一致） */
export function flatLineOption(color = '#CBD5E1'): any {
  return {
    grid: { left: 2, right: 2, top: 6, bottom: 2 },
    xAxis: { type: 'category', show: false, data: [0, 1, 2, 3, 4, 5, 6] },
    yAxis: { type: 'value', show: false },
    series: [{
      type: 'line', symbol: 'none', data: [0, 0, 0, 0, 0, 0, 0],
      lineStyle: { width: 1.5, color, type: 'dashed' },
    }],
  }
}
