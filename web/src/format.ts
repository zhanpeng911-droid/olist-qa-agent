/** 数值/统计量业务化格式化 */

/** API 可能从数据库返回 Decimal 的字符串形式；统一安全转换。 */
export function finiteNumber(v: any): number | null {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

/** p 值：>=0.001 四位小数；<0.001 显示 '< 0.001'；极小值 2 位科学计数 */
export function fmtP(p: any): string {
  const v = finiteNumber(p)
  if (v == null) return '—'
  if (v === 0) return '< 1e-300'
  if (v < 0.001) return v < 1e-6 ? v.toExponential(2) : '< 0.001'
  return v.toFixed(4)
}

/** 一般数值保留 digits 位小数 */
export function fmtNum(v: any, digits = 2): string {
  const n = finiteNumber(v)
  return n == null ? '—' : n.toFixed(digits)
}

/** 小数比率转百分比；兼容 number 与数值字符串。 */
export function fmtPct(v: any, digits = 1): string {
  const n = finiteNumber(v)
  return n == null ? '—' : (n * 100).toFixed(digits) + '%'
}
