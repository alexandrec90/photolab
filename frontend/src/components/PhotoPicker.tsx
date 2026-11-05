import { useEffect, useState } from 'react'

import { api, thumbUrl } from '../api/client'
import type { PhotoSummary, StockResult } from '../api/types'

interface Props {
  title: string
  selected?: number | null
  /** Several at once: picking one is the caller's toggle. */
  selection?: number[]
  onSelect: (id: number) => void
  allowStock?: boolean
  stockReady?: boolean
}

function StockResults({ query, onSelect }: { query: string; onSelect: (id: number) => void }) {
  const [results, setResults] = useState<StockResult[]>([])
  const [problem, setProblem] = useState('')

  useEffect(() => {
    if (!query) return
    let current = true
    api
      .searchStock(query)
      .then((found) => current && setResults(found))
      .catch((error: Error) => current && setProblem(error.message))
    return () => {
      current = false
    }
  }, [query])

  async function bringIn(result: StockResult) {
    try {
      onSelect((await api.importStock(result.id)).id)
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <>
      {problem && <p className="problem">{problem}</p>}
      <div className="picks">
        {results.map((result) => (
          <button
            key={result.id}
            className="pick"
            title={`${result.alt} (${result.photographer})`}
            onClick={() => bringIn(result)}
          >
            <img src={result.preview_url} alt={result.alt} loading="lazy" />
          </button>
        ))}
      </div>
    </>
  )
}

export function PhotoPicker({
  title,
  selected,
  selection,
  onSelect,
  allowStock,
  stockReady,
}: Props) {
  const [search, setSearch] = useState('')
  const [term, setTerm] = useState('')
  const [photos, setPhotos] = useState<PhotoSummary[]>([])
  const [stock, setStock] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setTerm(search.trim()), 250)
    return () => clearTimeout(timer)
  }, [search])

  useEffect(() => {
    if (stock) return
    let current = true
    void api.photos({ q: term, limit: 24 }).then((page) => current && setPhotos(page.items))
    return () => {
      current = false
    }
  }, [term, stock])

  return (
    <section className="picker">
      <header>
        <h3>{title}</h3>
        {allowStock && (
          <div className="source" role="group" aria-label="Source">
            <button aria-pressed={!stock} onClick={() => setStock(false)}>
              Album
            </button>
            <button
              aria-pressed={stock}
              disabled={!stockReady}
              title={stockReady ? '' : 'Add PEXELS_API_KEY to .env'}
              onClick={() => setStock(true)}
            >
              Stock
            </button>
          </div>
        )}
      </header>
      <input
        type="search"
        aria-label={`Search for ${title}`}
        placeholder={stock ? 'beach at sunset' : 'Search the album'}
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      {stock ? (
        <StockResults query={term} onSelect={onSelect} />
      ) : (
        <div className="picks">
          {photos.map((photo) => (
            <button
              key={photo.id}
              className="pick"
              aria-pressed={selection?.includes(photo.id) ?? selected === photo.id}
              title={photo.caption ?? photo.path}
              onClick={() => onSelect(photo.id)}
            >
              <img src={thumbUrl(photo.id)} alt={photo.caption ?? photo.path} loading="lazy" />
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
