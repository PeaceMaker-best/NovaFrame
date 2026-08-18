import assert from 'node:assert/strict'
import test from 'node:test'
import {
  isMatrixCellSelected,
  matrixAxisSelectionState,
  matrixCellKey,
  selectAllMatrixCells,
  selectedMatrixItems,
  toggleMatrixCell,
  toggleMatrixColumn,
  toggleMatrixRow,
} from '../src/lib/matrixSelection.ts'
import {
  candidateReviewGroupId,
  groupReviewCandidates,
} from '../src/lib/reviewGrouping.ts'
import { appendUniqueById, initialStableTotal } from '../src/lib/pagination.ts'
import { resolveProviderEstimate } from '../src/lib/providerRouting.ts'

const shots = ['main', 'size', 'lifestyle-scene']

test('matrix cells toggle independently without creating a Cartesian product', () => {
  let selection = new Set()
  selection = toggleMatrixCell(selection, '单品', 'main')
  selection = toggleMatrixCell(selection, '旅行收纳袋', 'size')

  assert.equal(isMatrixCellSelected(selection, '单品', 'size'), false)
  assert.deepEqual(selectedMatrixItems(selection, ['单品', '旅行收纳袋'], shots), [
    { task: '单品', shot: 'main' },
    { task: '旅行收纳袋', shot: 'size' },
  ])
})

test('row, column, select-all, and partial axis states preserve exact cells', () => {
  let selection = toggleMatrixRow(new Set(), '单品', shots)
  assert.equal(matrixAxisSelectionState(selection, shots.map((shot) => matrixCellKey('单品', shot))), 'all')

  selection = toggleMatrixColumn(selection, ['单品', '旅行收纳袋'], 'main')
  assert.equal(isMatrixCellSelected(selection, '单品', 'main'), true)
  assert.equal(isMatrixCellSelected(selection, '旅行收纳袋', 'main'), true)

  selection = toggleMatrixColumn(selection, ['单品', '旅行收纳袋'], 'main')
  assert.equal(isMatrixCellSelected(selection, '单品', 'main'), false)
  assert.equal(isMatrixCellSelected(selection, '旅行收纳袋', 'main'), false)

  selection = toggleMatrixCell(selection, '旅行收纳袋', 'main')
  assert.equal(
    matrixAxisSelectionState(selection, ['单品', '旅行收纳袋'].map((task) => matrixCellKey(task, 'main'))),
    'partial',
  )

  const all = selectAllMatrixCells(['单品', '旅行收纳袋'], shots)
  assert.equal(all.size, 6)
})

function candidate(overrides) {
  return {
    id: 'candidate-1',
    jobId: 'job-1',
    product: 'product-1',
    task: '单品',
    shot: 'main',
    variant: 1,
    url: '/candidate.png',
    reviewStatus: 'pending',
    ...overrides,
  }
}

test('review groups isolate product and job in addition to task and shot', () => {
  const candidates = [
    candidate({ id: 'a' }),
    candidate({ id: 'b', jobId: 'job-2' }),
    candidate({ id: 'c', product: 'product-2' }),
    candidate({ id: 'd', task: '旅行收纳袋' }),
    candidate({ id: 'e', shot: 'size' }),
  ]

  assert.equal(new Set(candidates.map(candidateReviewGroupId)).size, 5)
  assert.equal(groupReviewCandidates(candidates).length, 5)
})

test('review group keys do not collide when values contain separators', () => {
  const first = candidate({ task: 'a::b', shot: 'main' })
  const second = candidate({ task: 'a', shot: 'main', product: 'product-1::b' })

  assert.notEqual(candidateReviewGroupId(first), candidateReviewGroupId(second))
})

function providerChannel(id, rate, overrides = {}) {
  return {
    id,
    name: id,
    baseUrl: 'https://example.test',
    endpoint: '/images',
    apiKeyHint: '',
    hasApiKey: true,
    model: 'image-model',
    active: true,
    currency: 'CNY',
    rates: { low: rate, medium: rate, high: rate },
    createdAt: '',
    updatedAt: '',
    ...overrides,
  }
}

test('missing fixed provider never falls back to a default or auto quote', () => {
  const config = {
    channels: [providerChannel('available', 0.1)],
    routing: { mode: 'fixed', fixedChannelId: 'removed', currency: 'CNY' },
    summary: { channelCount: 1, activeChannelCount: 1, pricedChannelCount: 1 },
  }

  const explicit = resolveProviderEstimate(config, 'removed', 'low')
  assert.equal(explicit.fixedUnavailable, true)
  assert.equal(explicit.effectiveChannel, undefined)

  const workspaceDefault = resolveProviderEstimate(config, 'default', 'low')
  assert.equal(workspaceDefault.effectiveChannel, undefined)
})

test('candidate pagination keeps first occurrence of each stable id', () => {
  const candidates = new Map()
  appendUniqueById(candidates, [{ id: 'a', value: 1 }, { id: 'b', value: 2 }])
  appendUniqueById(candidates, [{ id: 'b', value: 9 }, { id: 'c', value: 3 }])

  assert.deepEqual([...candidates.values()], [
    { id: 'a', value: 1 },
    { id: 'b', value: 2 },
    { id: 'c', value: 3 },
  ])
  assert.equal(initialStableTotal(200, 500), 500)
})
