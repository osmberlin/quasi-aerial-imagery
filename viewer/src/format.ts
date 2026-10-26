/** Number formatting for UI text, with a German decimal comma. */
export function formatNumber(value: number, fractionDigits = 2): string {
  return value.toLocaleString('de-DE', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}
