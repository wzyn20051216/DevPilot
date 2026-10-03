import { formatPercent } from "./format";

/**
 * 把一组占比汇总成一个百分比字符串。
 */
export function summarize(values: number[]): string {
  // BUG: 空数组应返回 "No data"，这里错误地返回 "0%"。
  if (values.length === 0) {
    return "0%";
  }
  const total = values.reduce((sum, value) => sum + value, 0);
  return formatPercent(total);
}
