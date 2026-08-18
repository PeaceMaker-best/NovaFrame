import type { GenerationRunItem, ShotType } from '../types'

export type MatrixCellSelection = ReadonlySet<string>
export type MatrixAxisSelectionState = 'none' | 'partial' | 'all'

export function matrixCellKey(task: string, shot: ShotType): string {
  return JSON.stringify([task, shot])
}

export function isMatrixCellSelected(
  selection: MatrixCellSelection,
  task: string,
  shot: ShotType,
): boolean {
  return selection.has(matrixCellKey(task, shot))
}

export function selectAllMatrixCells(
  tasks: readonly string[],
  shots: readonly ShotType[],
): Set<string> {
  return new Set(tasks.flatMap((task) => shots.map((shot) => matrixCellKey(task, shot))))
}

export function toggleMatrixCell(
  selection: MatrixCellSelection,
  task: string,
  shot: ShotType,
): Set<string> {
  const next = new Set(selection)
  const key = matrixCellKey(task, shot)
  next.has(key) ? next.delete(key) : next.add(key)
  return next
}

function toggleMatrixAxis(
  selection: MatrixCellSelection,
  keys: readonly string[],
): Set<string> {
  const next = new Set(selection)
  const allSelected = keys.length > 0 && keys.every((key) => selection.has(key))
  keys.forEach((key) => allSelected ? next.delete(key) : next.add(key))
  return next
}

export function toggleMatrixRow(
  selection: MatrixCellSelection,
  task: string,
  shots: readonly ShotType[],
): Set<string> {
  return toggleMatrixAxis(selection, shots.map((shot) => matrixCellKey(task, shot)))
}

export function toggleMatrixColumn(
  selection: MatrixCellSelection,
  tasks: readonly string[],
  shot: ShotType,
): Set<string> {
  return toggleMatrixAxis(selection, tasks.map((task) => matrixCellKey(task, shot)))
}

export function matrixAxisSelectionState(
  selection: MatrixCellSelection,
  keys: readonly string[],
): MatrixAxisSelectionState {
  if (!keys.length) return 'none'
  const selectedCount = keys.filter((key) => selection.has(key)).length
  if (!selectedCount) return 'none'
  return selectedCount === keys.length ? 'all' : 'partial'
}

export function selectedMatrixItems(
  selection: MatrixCellSelection,
  tasks: readonly string[],
  shots: readonly ShotType[],
): GenerationRunItem[] {
  return tasks.flatMap((task) => shots
    .filter((shot) => isMatrixCellSelected(selection, task, shot))
    .map((shot) => ({ task, shot })))
}
