import type { GenerationCandidate } from '../types'

export interface CandidateReviewGroup {
  id: string
  product: string
  jobId: string
  task: string
  shot: GenerationCandidate['shot']
  items: GenerationCandidate[]
}

export function candidateReviewGroupId(candidate: GenerationCandidate): string {
  return JSON.stringify([
    candidate.product,
    candidate.jobId,
    candidate.task,
    candidate.shot,
  ])
}

export function groupReviewCandidates(
  candidates: readonly GenerationCandidate[],
): CandidateReviewGroup[] {
  const groups = new Map<string, CandidateReviewGroup>()
  candidates.forEach((candidate) => {
    const id = candidateReviewGroupId(candidate)
    const existing = groups.get(id)
    if (existing) {
      existing.items.push(candidate)
      return
    }
    groups.set(id, {
      id,
      product: candidate.product,
      jobId: candidate.jobId,
      task: candidate.task,
      shot: candidate.shot,
      items: [candidate],
    })
  })
  return [...groups.values()]
}
