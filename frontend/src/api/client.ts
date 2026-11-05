import type {
  Advice,
  CompositeSettings,
  Generation,
  PhotoDetail,
  PhotoEdit,
  PhotoPage,
  PhotoQuery,
  PhotoSummary,
  Recipe,
  Stats,
  StockResult,
  Suggestion,
} from './types'

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = typeof body.detail === 'string' ? body.detail : response.statusText
    throw new ApiError(detail)
  }
  return response.json() as Promise<T>
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
}

export function toQueryString(params: object): string {
  const entries = Object.entries(params).filter(
    ([, value]) => value !== undefined && value !== '',
  )
  const query = new URLSearchParams(entries.map(([key, value]) => [key, String(value)]))
  return query.size ? `?${query}` : ''
}

export const thumbUrl = (id: number) => `/api/photos/${id}/thumb`
export const imageUrl = (id: number) => `/api/photos/${id}/image`

export const api = {
  stats: () => request<Stats>('/stats'),
  photos: (query: PhotoQuery) => request<PhotoPage>(`/photos${toQueryString(query)}`),
  photo: (id: number) => request<PhotoDetail>(`/photos/${id}`),
  editPhoto: (id: number, edit: PhotoEdit) =>
    request<PhotoDetail>(`/photos/${id}`, { method: 'PATCH', body: JSON.stringify(edit) }),
  scan: () => post('/library/scan'),
  annotate: (limit?: number, backend?: string) => post('/annotations/run', { limit, backend }),
  suggest: (prompt: string) => post<Suggestion>('/ai/suggest', { prompt }),
  imagine: (prompt: string, referenceIds: number[]) =>
    post<Generation>('/generations', { kind: 'ai', prompt, reference_ids: referenceIds }),
  relight: (photoId: number) =>
    post<Generation>('/generations', { kind: 'ai', preset: 'relight', reference_ids: [photoId] }),
  searchStock: (q: string) => request<StockResult[]>(`/stock/search${toQueryString({ q })}`),
  importStock: (pexelsId: number) =>
    post<PhotoSummary>('/stock/import', { pexels_id: pexelsId }),
  advice: (subjectId: number, backgroundId: number) =>
    request<Advice>(
      `/compose/advice${toQueryString({ subject_id: subjectId, background_id: backgroundId })}`,
    ),
  enhance: (photoId: number, recipe: Recipe) =>
    post<Generation>('/generations', { kind: 'enhance', photo_id: photoId, recipe }),
  composite: (subjectId: number, backgroundId: number, settings: CompositeSettings) =>
    post<Generation>('/generations', {
      kind: 'composite',
      subject_id: subjectId,
      background_id: backgroundId,
      ...settings,
    }),
  generations: () => request<Generation[]>('/generations'),
}
