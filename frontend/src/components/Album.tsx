import { useEffect, useState } from 'react'

import { api, thumbUrl } from '../api/client'
import type { PhotoQuery, PhotoSummary } from '../api/types'

const PAGE = 60
const ROW_HEIGHT = 220

interface Props {
  query: PhotoQuery
  title: string
  // Bumped by the parent when something it did may have changed this listing.
  version: number
  onOpen: (photo: PhotoSummary, listing: PhotoSummary[]) => void
}

function Tile({ photo, onOpen }: { photo: PhotoSummary; onOpen: () => void }) {
  const aspect = photo.width / photo.height
  const label = photo.caption ?? photo.path
  // A justified row with no layout code: each tile grows in proportion to its aspect.
  const style = { flexGrow: aspect, flexBasis: aspect * ROW_HEIGHT }
  return (
    <button className="tile" style={style} onClick={onOpen} aria-label={label}>
      <img src={thumbUrl(photo.id)} alt="" loading="lazy" />
      <span className="mark" data-review={photo.review_status} title={photo.review_status} />
      {photo.origin === 'generated' && <span className="badge">Output</span>}
      {photo.annotation_error && <span className="badge failed">Failed</span>}
      <span className="caption">{label}</span>
    </button>
  )
}

export function Album({ query, title, version, onOpen }: Props) {
  const [items, setItems] = useState<PhotoSummary[]>([])
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState('')
  const [term, setTerm] = useState('')
  const [problem, setProblem] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setTerm(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])

  const key = JSON.stringify(query)
  useEffect(() => {
    let current = true
    api
      .photos({ ...query, q: term, limit: PAGE })
      .then((page) => {
        if (current) {
          setItems(page.items)
          setTotal(page.total)
          setProblem('')
        }
      })
      .catch((error: Error) => current && setProblem(error.message))
    return () => {
      current = false
    }
    // `key` stands for `query`, which is a new object on every render of the parent.
  }, [key, term, version])

  async function loadMore() {
    const page = await api.photos({ ...query, q: term, limit: PAGE, offset: items.length })
    // A photo added since the last page shifts the offsets and repeats a row.
    const seen = new Set(items.map((item) => item.id))
    setItems([...items, ...page.items.filter((item) => !seen.has(item.id))])
  }

  return (
    <main className="album">
      <header className="album-head">
        <h2>{title}</h2>
        <span className="total">{total}</span>
        <input
          type="search"
          placeholder="Search captions, tags, people"
          aria-label="Search photos"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </header>
      {problem && (
        <p className="problem" role="alert">
          {problem}
        </p>
      )}
      {items.length === 0 && !problem && (
        <p className="empty">
          {term ? 'Nothing matches that search.' : 'Nothing here yet.'}
        </p>
      )}
      <div className="grid">
        {items.map((photo) => (
          <Tile key={photo.id} photo={photo} onOpen={() => onOpen(photo, items)} />
        ))}
        {/* Stops the last row's few tiles stretching to fill the whole width. */}
        <span className="grid-tail" />
      </div>
      {items.length < total && (
        <button className="more" onClick={loadMore}>
          Show more
        </button>
      )}
    </main>
  )
}
