/** 数值/统计量业务化格式化 */

/** p 值：>=0.001 四位小数；<0.001 显示 '< 0.001'；极小值 2 位科学计数 */
export function fmtP(p: any): string {
  if (p == null || isNaN(Number(p))) return '—'
  const v = Number(p)
  if (v < 0.001) return v < 1e-6 ? v.toExponential(2) : '< 0.001'
  return v.toFixed(4)
}

/** 一般数值保留 digits 位小数 */
export function fmtNum(v: any, digits = 2): string {
  if (v == null || isNaN(Number(v))) return '—'
  return Number(v).toFixed(digits)
}
