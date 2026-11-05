export type ReviewStatus = 'pending' | 'approved' | 'rejected'
export type Light = 'left' | 'right' | 'front' | 'back' | 'above' | 'diffuse'
export type Recipe = 'natural' | 'film' | 'golden_hour'

export interface PhotoSummary {
  id: number
  origin: 'library' | 'stock' | 'generated'
  path: string
  width: number
  height: number
  taken_at: string | null
  review_status: ReviewStatus
  people: string[]
  caption: string | null
  annotated: boolean
  annotation_error: string | null
}

export type AnnotationData = Record<string, unknown>

export interface AnnotationView {
  data: AnnotationData
  original: AnnotationData
  model: string
  prompt_version: number
  updated_at: string
}

export interface Generation {
  id: number
  kind: 'enhance' | 'composite' | 'ai'
  params: Record<string, unknown>
  status: 'queued' | 'running' | 'done' | 'failed'
  error: string | null
  subject_id: number | null
  background_id: number | null
  output_id: number | null
  created_at: string
  finished_at: string | null
}

export interface PhotoDetail extends PhotoSummary {
  format: string
  exif: Record<string, unknown>
  source: Record<string, unknown>
  annotation: AnnotationView | null
  made_by: Generation | null
}

export interface PhotoPage {
  total: number
  items: PhotoSummary[]
}

export interface Job {
  name: string
  running: boolean
  done: number
  failed: number
  total: number
  message: string
}

export interface Stats {
  photos: number
  outputs: number
  annotated: number
  unannotated: number
  failed: number
  to_review: number
  approved: number
  rejected: number
  annotation_backends: string[]
  annotation_backend: string
  annotation_model: string
  annotation_ready: boolean
  image_ready: boolean
  stock_ready: boolean
  jobs: Job[]
}

export interface Suggestion {
  reference_ids: number[]
  why: string
}

export interface Advice {
  light: Light
  flip: boolean
  warnings: string[]
}

export interface StockResult {
  id: number
  preview_url: string
  photographer: string
  alt: string
}

export interface PhotoQuery {
  q?: string
  origin?: string
  review?: string
  annotated?: boolean
  reviewable?: boolean
  limit?: number
  offset?: number
}

export interface PhotoEdit {
  review_status?: ReviewStatus
  people?: string[]
  annotation?: AnnotationData
}

export interface CompositeSettings {
  center_x: number
  baseline_y: number
  height: number
  flip: boolean
  color_match: number
  shadow_strength: number
  light_wrap: number
  light: Light
}
