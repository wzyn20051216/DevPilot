/**
 * 把 0~1 的小数格式化成百分比字符串。
 */
export function formatPercent(value: number): string {
  // BUG: 用 Math.trunc 截断小数，导致 0.666 -> "66%" 而非四舍五入的 "67%"。
  return `${Math.trunc(value * 100)}%`;
}
