import * as echarts from 'echarts'

/** 平滑面积图（青蓝渐变 → 透明）：去常驻数据点，hover 动态点亮 */
export function areaOption(x: string[], y: number[], name = ''): any {
  return {
    tooltip: {
      trigger: 'axis', backgroundColor: '#1E2238', borderWidth: 0, padding: [10, 14],
      textStyle: { color: '#fff', fontSize: 12 },
      axisPointer: { type: 'line', lineStyle: { color: 'rgba(255,255,255,.25)' } },
    },
    grid: { left: 44, right: 20, top: 20, bottom: 28 },
    xAxis: { type: 'category', data: x, axisLine: { lineStyle: { color: '#E2E8F0' } }, axisLabel: { color: '#94A3B8' } },
    yAxis: { type: 'value', axisLabel: { color: '#94A3B8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
    series: [{
      name, type: 'line', smooth: 0.35, symbol: 'circle', symbolSize: 7,
      showSymbol: false,   // 平时隐藏数据点，hover 动态点亮
      hoverAnimation: true,
      data: y.map(v => +(v * 100).toFixed(1)),
      lineStyle: { width: 3, color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#00C5FF' }, { offset: 1, color: '#2F65F6' }]) },
      itemStyle: { color: '#2F65F6' },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(47,101,246,.25)' },
          { offset: 1, color: 'rgba(47,101,246,0)' },
        ]),
      },
    }],
  }
}

/** 横向条形图（排行榜）：浅灰底槽 + 胶囊圆角 + 青蓝渐变 + 右侧大号百分比 */
export function barOption(
  labels: string[], values: number[], name = '',
  opts: { max?: number; baseline?: number; baselineLabel?: string; samples?: number[] } = {},
): any {
  const display = values.map(v => +(v * 100).toFixed(1))
  const axisMax = opts.max ?? Math.max(5, Math.ceil(Math.max(...display, 0) / 5) * 5)
  const sampleLabel = (index: number) => {
    const sample = opts.samples?.[index]
    return sample == null ? '' : ` · n=${Number(sample).toLocaleString()}`
  }
  return {
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      backgroundColor: '#1E2238', borderWidth: 0, textStyle: { color: '#fff' },
      formatter: (params: any[]) => {
        const row = params.find((p: any) => p.seriesName === name) ?? params.at(-1)
        return `${row?.name ?? ''}<br/>${name}：${row?.value ?? '—'}%${sampleLabel(row?.dataIndex ?? 0)}`
      },
    },
    grid: { left: 90, right: opts.samples?.length ? 122 : 56, top: 26, bottom: 24 },
    xAxis: { type: 'value', max: axisMax, axisLabel: { color: '#94A3B8', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#F1F5F9' } } },
    yAxis: { type: 'category', inverse: true, data: labels, axisLabel: { color: '#64748B' }, axisLine: { show: false }, axisTick: { show: false } },
    series: [
      // 底槽：极浅灰全长圆角轨道
      {
        name: '', type: 'bar', barWidth: 18, barGap: '-100%', silent: true,
        data: display.map(() => axisMax),
        itemStyle: { color: '#F1F5F9', borderRadius: 9 },
      },
      // 前景：青蓝 → 电光蓝渐变胶囊
      {
        name, type: 'bar', barWidth: 18, data: display,
        label: {
          show: true, position: 'right', color: '#0F172A', fontSize: 12,
          fontWeight: 700,
          formatter: (p: any) => `${p.value}%${sampleLabel(p.dataIndex)}`,
        },
        itemStyle: {
          borderRadius: 9,
          color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: '#00C5FF' }, { offset: 1, color: '#2F65F6' }]),
        },
        markLine: typeof opts.baseline === 'number' ? {
          silent: true, symbol: 'none',
          lineStyle: { color: '#F59E0B', width: 1.5, type: 'dashed' },
          label: { show: false },
          data: [{ xAxis: +(opts.baseline * 100).toFixed(2) }],
        } : undefined,
      },
    ],
  }
}

/** 两条比例趋势线：用于同时观察低评分率与延迟率的月度变化。 */
export function rateTrendOption(
  x: string[], lowScore: number[], late: number[],
): any {
  return {
    tooltip: {
      trigger: 'axis', backgroundColor: '#1E2238', borderWidth: 0,
      textStyle: { color: '#fff', fontSize: 12 },
      valueFormatter: (value: number) => `${Number(value).toFixed(1)}%`,
    },
    legend: {
      top: 0, right: 18, itemWidth: 18, itemHeight: 8,
      textStyle: { color: '#64748B', fontSize: 12 },
    },
    grid: { left: 44, right: 20, top: 38, bottom: 28 },
    xAxis: {
      type: 'category', data: x,
      axisLine: { lineStyle: { color: '#E2E8F0' } },
      axisLabel: { color: '#94A3B8' },
    },
    yAxis: {
      type: 'value', axisLabel: { color: '#94A3B8', formatter: '{value}%' },
      splitLine: { lineStyle: { color: '#F1F5F9' } },
    },
    series: [
      {
        name: '低评分率', type: 'line', smooth: 0.3, showSymbol: false,
        data: lowScore.map(v => +(v * 100).toFixed(1)),
        lineStyle: { width: 3, color: '#2F65F6' }, itemStyle: { color: '#2F65F6' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(47,101,246,.20)' },
            { offset: 1, color: 'rgba(47,101,246,0)' },
          ]),
        },
      },
      {
        name: '延迟率', type: 'line', smooth: 0.3, showSymbol: false,
        data: late.map(v => +(v * 100).toFixed(1)),
        lineStyle: { width: 2.5, color: '#F59E0B' },
        itemStyle: { color: '#F59E0B' },
      },
    ],
  }
}

/** 环形图：加粗圆角环体 + 中心大数字 + 次项白字标注（图例由 HTML 网格承载） */
export function donutOption(labels: string[], values: number[], centerNumber?: string | number): any {
  const total = values.reduce((a, b) => a + b, 0)
  return {
    tooltip: {
      trigger: 'item', backgroundColor: '#1E2238', textStyle: { color: '#fff' },
      formatter: (p: any) => `${p.name}<br/>${p.value.toLocaleString()} 单 (${((p.value / total) * 100).toFixed(0)}%)`,
    },
    legend: { show: false },   // 图例改由页面 HTML 2×2 网格渲染
    graphic: [
      // 中心核心大数字（总低评订单数）
      { type: 'text', left: 'center', top: '36%', style: { text: Number(centerNumber ?? total).toLocaleString(), textAlign: 'center', fill: '#0F172A', fontSize: 30, fontWeight: 700 } },
      { type: 'text', left: 'center', top: '52%', style: { text: '低评订单 · 有效样本', textAlign: 'center', fill: '#64748B', fontSize: 12 } },
    ],
    series: [{
      type: 'pie', radius: ['62%', '84%'], center: ['50%', '47%'],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 3 },
      label: { show: false },   // 环体干净：不印浮动百分比文字
      labelLine: { show: false },
      emphasis: { label: { show: false } },
      data: labels.map((n, i) => ({ name: n, value: values[i] })),
      color: ['#2F65F6', '#00C5FF', '#38BDF8', '#10B981', '#F59E0B', '#F43F5E', '#8B5CF6'],
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
