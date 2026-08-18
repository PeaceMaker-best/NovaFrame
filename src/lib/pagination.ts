export function appendUniqueById<T extends { id: string }>(
  target: Map<string, T>,
  items: T[],
): void {
  items.forEach((item) => {
    if (item.id && !target.has(item.id)) target.set(item.id, item)
  })
}

export function initialStableTotal(reportedTotal: number, pageLength: number): number {
  return Math.max(0, pageLength, reportedTotal)
}
