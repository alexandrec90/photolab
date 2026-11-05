import { useEffect, useState } from 'react'

import { api, thumbUrl } from '../api/client'
import type { Generation } from '../api/types'

/** Recent generations of one kind, kept current while they render. */
export function useRuns(kind: Generation['kind'], version: number) {
  const [runs, setRuns] = useState<Generation[]>([])

  useEffect(() => {
    let current = true
    const load = () => api.generations().then((rows) => current && setRuns(rows))
    void load()
    const timer = setInterval(() => void load(), 2000)
    return () => {
      current = false
      clearInterval(timer)
    }
  }, [version])

  return runs.filter((run) => run.kind === kind)
}

export function Runs({ runs, onOpen }: { runs: Generation[]; onOpen: (id: number) => void }) {
  return (
    <ul className="runs">
      {runs.map((run) => (
        <li key={run.id} data-status={run.status}>
          {run.output_id ? (
            <button onClick={() => onOpen(run.output_id as number)}>
              <img src={thumbUrl(run.output_id)} alt={`Output of run ${run.id}`} />
            </button>
          ) : (
            <span className="pending">{run.error ?? `${run.status}…`}</span>
          )}
        </li>
      ))}
    </ul>
  )
}
