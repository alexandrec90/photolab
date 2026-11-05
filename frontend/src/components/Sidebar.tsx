import { useState } from 'react'

import { api } from '../api/client'
import type { Job, Stats } from '../api/types'
import { VIEWS } from '../views'

interface Props {
  stats: Stats | null
  offline: boolean
  active: string
  onSelect: (view: string) => void
  onChanged: () => void
}

function JobLine({ job }: { job: Job }) {
  const progress = job.total ? ` ${job.done}/${job.total}` : ''
  return (
    <li className="job" data-running={job.running}>
      <strong>{job.name}</strong>
      <span>{job.running ? `running${progress}` : job.message}</span>
    </li>
  )
}

const SAMPLE_SIZE = 20

interface AnnotateProps {
  stats: Stats | null
  busy: boolean
  run: (action: () => Promise<unknown>) => void
}

function AnnotateControls({ stats, busy, run }: AnnotateProps) {
  const [choice, setChoice] = useState('')
  const backend = choice || stats?.annotation_backend
  const unannotated = stats?.unannotated ?? 0
  const blocked = busy || unannotated === 0

  return (
    <>
      {(stats?.annotation_backends.length ?? 0) > 1 && (
        <label className="backend">
          <span>Annotate with</span>
          <select value={backend} onChange={(event) => setChoice(event.target.value)}>
            {stats?.annotation_backends.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
      )}
      <div className="annotate">
        <button
          disabled={blocked}
          title={`Sends ${unannotated} photos to ${backend ?? 'the model'}`}
          onClick={() => run(() => api.annotate(undefined, backend))}
        >
          Annotate {unannotated || ''}
        </button>
        <button
          disabled={blocked}
          title={`Annotates ${SAMPLE_SIZE} photos, to audit before committing to a whole library`}
          onClick={() => run(() => api.annotate(SAMPLE_SIZE, backend))}
        >
          Try {SAMPLE_SIZE}
        </button>
      </div>
      {stats && !stats.annotation_ready && (
        <p className="hint">
          Sign in to the codex or claude command, or add ANTHROPIC_API_KEY to .env, to annotate.
        </p>
      )}
    </>
  )
}

export function Sidebar({ stats, offline, active, onSelect, onChanged }: Props) {
  const [problem, setProblem] = useState('')

  async function run(action: () => Promise<unknown>) {
    setProblem('')
    try {
      await action()
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error))
    }
    onChanged()
  }

  const running = (name: string) => stats?.jobs.some((j) => j.name === name && j.running)

  return (
    <aside className="sidebar">
      <h1 className="brand">Photolab</h1>
      <nav aria-label="Album">
        {VIEWS.map((view) => (
          <button
            key={view.id}
            className="shelf"
            aria-current={active === view.id ? 'page' : undefined}
            onClick={() => onSelect(view.id)}
          >
            <span>{view.label}</span>
            <span className="count">{stats ? view.count(stats) : ''}</span>
          </button>
        ))}
        <button
          className="shelf compose-link"
          aria-current={active === 'compose' ? 'page' : undefined}
          onClick={() => onSelect('compose')}
        >
          Compose
        </button>
        <button
          className="shelf"
          aria-current={active === 'imagine' ? 'page' : undefined}
          onClick={() => onSelect('imagine')}
        >
          Imagine
        </button>
      </nav>

      <div className="actions">
        <button disabled={Boolean(running('scan'))} onClick={() => run(api.scan)}>
          Scan library
        </button>
        <AnnotateControls stats={stats} busy={Boolean(running('annotate'))} run={run} />
        {problem && (
          <p className="problem" role="alert">
            {problem}
          </p>
        )}
        {offline && (
          <p className="problem" role="alert">
            The API is not reachable.
          </p>
        )}
        <ul className="jobs">
          {stats?.jobs.map((job) => (
            <JobLine key={job.name} job={job} />
          ))}
        </ul>
      </div>
    </aside>
  )
}
