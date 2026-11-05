import type { PhotoQuery, Stats } from './api/types'

export interface AlbumView {
  id: string
  label: string
  query: PhotoQuery
  count: (stats: Stats) => number
}

/** The album's shelves. Each is just a saved query, so there is one grid to maintain. */
export const VIEWS: AlbumView[] = [
  { id: 'all', label: 'All photos', query: {}, count: (s) => s.photos + s.outputs },
  {
    id: 'review',
    label: 'To review',
    query: { reviewable: true },
    count: (s) => s.to_review,
  },
  { id: 'outputs', label: 'Outputs', query: { origin: 'generated' }, count: (s) => s.outputs },
  {
    id: 'unannotated',
    label: 'Not annotated',
    query: { annotated: false },
    count: (s) => s.unannotated,
  },
  { id: 'approved', label: 'Approved', query: { review: 'approved' }, count: (s) => s.approved },
  { id: 'rejected', label: 'Rejected', query: { review: 'rejected' }, count: (s) => s.rejected },
]
