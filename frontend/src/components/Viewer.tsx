import { useCallback, useEffect, useState } from 'react'

import { api, imageUrl, thumbUrl } from '../api/client'
import type {
  AnnotationData,
  Generation,
  PhotoDetail,
  PhotoEdit,
  PhotoSummary,
  Recipe,
  ReviewStatus,
} from '../api/types'
import { AnnotationEditor, parseList } from './AnnotationEditor'

interface Props {
  photo: PhotoSummary
  listing: PhotoSummary[]
  onNavigate: (photo: PhotoSummary) => void
  onClose: () => void
  onChanged: () => void
}

const RECIPES: [Recipe, string][] = [
  ['natural', 'Natural'],
  ['film', 'Film'],
  ['golden_hour', 'Golden hour'],
]

const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)

/** Every photo an output was made from, and an AI image can have more than two. */
function inputsOf(made: Generation): number[] {
  if (made.kind === 'ai') return (made.params.reference_ids as number[] | undefined) ?? []
  return [made.subject_id, made.background_id].filter((id) => id !== null)
}

function recipeOf(made: Generation): string {
  if (made.kind === 'enhance') return `Recipe: ${String(made.params.recipe)}`
  if (made.kind === 'composite') return `Composite, ${String(made.params.light)} light`
  if (made.params.preset === 'relight') return 'AI image: relit by Codex'
  return `AI image: ${String(made.params.prompt)}`
}

function Lineage({ made }: { made: Generation }) {
  const inputs = inputsOf(made)
  return (
    <section className="lineage">
      <h3>Made from</h3>
      <div className="inputs">
        {inputs.map((id) => (
          <img key={id} src={thumbUrl(id)} alt={`Input photo ${id}`} />
        ))}
      </div>
      <p>{recipeOf(made)}</p>
    </section>
  )
}

function Credit({ source }: { source: Record<string, unknown> }) {
  if (!source.photographer) return null
  return (
    <p className="credit">
      Stock photo by{' '}
      <a href={String(source.page_url)} target="_blank" rel="noreferrer">
        {String(source.photographer)}
      </a>
    </p>
  )
}

function usePhoto(id: number) {
  const [detail, setDetail] = useState<PhotoDetail | null>(null)
  useEffect(() => {
    let current = true
    setDetail(null)
    void api.photo(id).then((loaded) => current && setDetail(loaded))
    return () => {
      current = false
    }
  }, [id])
  return { detail, setDetail }
}

export function Viewer({ photo, listing, onNavigate, onClose, onChanged }: Props) {
  const { detail, setDetail } = usePhoto(photo.id)
  const [notice, setNotice] = useState('')

  const index = listing.findIndex((item) => item.id === photo.id)
  const step = useCallback(
    (by: number) => {
      const next = listing[index + by]
      if (next) onNavigate(next)
    },
    [index, listing, onNavigate],
  )

  const save = useCallback(
    async (edit: PhotoEdit) => {
      try {
        setDetail(await api.editPhoto(photo.id, edit))
        setNotice('')
        onChanged()
      } catch (error) {
        setNotice(error instanceof Error ? error.message : String(error))
      }
    },
    [photo.id, onChanged, setDetail],
  )

  const review = useCallback(
    async (status: ReviewStatus) => {
      await save({ review_status: status })
      step(1)
    },
    [save, step],
  )

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
      if (isTyping(event.target)) return
      if (event.key === 'ArrowRight') step(1)
      if (event.key === 'ArrowLeft') step(-1)
      if (event.key === 'a') void review('approved')
      if (event.key === 'r') void review('rejected')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, review, step])

  async function enhance(recipe: Recipe) {
    await api.enhance(photo.id, recipe)
    setNotice('Queued. The result will appear under Outputs.')
    onChanged()
  }

  async function relight() {
    try {
      await api.relight(photo.id)
      setNotice('Queued. This goes to OpenAI through Codex, and appears under Outputs.')
      onChanged()
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className="viewer" role="dialog" aria-label="Photo">
      <div className="stage">
        <img src={imageUrl(photo.id)} alt={detail?.annotation ? String(detail.annotation.data.caption) : ''} />
        <button className="close" aria-label="Close" onClick={onClose}>
          ×
        </button>
      </div>
      <aside className="panel">
        <p className="path">{photo.path}</p>
        <div className="verdict" data-review={detail?.review_status}>
          <button onClick={() => review('approved')}>
            Approve <kbd>A</kbd>
          </button>
          <button onClick={() => review('rejected')}>
            Reject <kbd>R</kbd>
          </button>
          <span>{detail?.review_status}</span>
        </div>
        {notice && <p className="notice" role="status">{notice}</p>}

        {detail && (
          <>
            {detail.made_by && <Lineage made={detail.made_by} />}
            <Credit source={detail.source} />
            <label className="people">
              <span>People in this photo</span>
              <input
                key={detail.id}
                defaultValue={detail.people.join(', ')}
                placeholder="Names, comma separated"
                onBlur={(event) => {
                  const people = parseList(event.target.value)
                  if (people.join() !== detail.people.join()) void save({ people })
                }}
              />
            </label>
            {detail.annotation ? (
              <AnnotationEditor
                key={detail.id}
                annotation={detail.annotation}
                onSave={(fields: AnnotationData) => void save({ annotation: fields })}
              />
            ) : (
              detail.origin !== 'generated' && (
                <p className="hint">{detail.annotation_error ?? 'Not annotated yet.'}</p>
              )
            )}
            <section className="recipes">
              <h3>New version</h3>
              {RECIPES.map(([recipe, name]) => (
                <button key={recipe} onClick={() => enhance(recipe)}>
                  {name}
                </button>
              ))}
              <button onClick={relight}>Relight with AI</button>
            </section>
          </>
        )}
      </aside>
    </div>
  )
}
