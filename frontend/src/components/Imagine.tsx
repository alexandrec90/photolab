import { useState } from 'react'

import { api, thumbUrl } from '../api/client'
import { PhotoPicker } from './PhotoPicker'
import { Runs, useRuns } from './Runs'

const MAX_REFERENCES = 6

interface Props {
  version: number
  imageReady: boolean
  onChanged: () => void
  onOpenOutput: (id: number) => void
}

export function Imagine({ version, imageReady, onChanged, onOpenOutput }: Props) {
  const [prompt, setPrompt] = useState('')
  const [references, setReferences] = useState<number[]>([])
  const [why, setWhy] = useState('')
  const [finding, setFinding] = useState(false)
  const [problem, setProblem] = useState('')
  const runs = useRuns('ai', version)

  function toggle(id: number) {
    setReferences((now) =>
      now.includes(id)
        ? now.filter((item) => item !== id)
        : [...now, id].slice(0, MAX_REFERENCES),
    )
  }

  async function attempt(action: () => Promise<void>) {
    setProblem('')
    try {
      await action()
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error))
    }
  }

  const find = () =>
    attempt(async () => {
      setFinding(true)
      try {
        const found = await api.suggest(prompt)
        setReferences(found.reference_ids.slice(0, MAX_REFERENCES))
        setWhy(found.why)
      } finally {
        setFinding(false)
      }
    })

  const start = () =>
    attempt(async () => {
      await api.imagine(prompt, references)
      onChanged()
    })

  return (
    <main className="compose">
      <header className="album-head">
        <h2>Imagine</h2>
      </header>
      <div className="compose-body">
        <div className="compose-pick">
          <PhotoPicker title="Photos to use" selection={references} onSelect={toggle} />
        </div>
        <div className="compose-controls">
          <label className="prompt">
            <span>What should it show?</span>
            <textarea
              rows={4}
              value={prompt}
              placeholder="Me walking on a beach at sunset, seen from a little way off"
              onChange={(event) => setPrompt(event.target.value)}
            />
          </label>
          <button disabled={!prompt.trim() || finding} onClick={find}>
            {finding ? 'Reading the catalog…' : 'Find photos in the album'}
          </button>
          {why && <p className="hint">{why}</p>}
          {references.length > 0 && (
            <div className="chosen">
              {references.map((id, index) => (
                <button
                  key={id}
                  title="Remove"
                  aria-label={`Remove photo ${index + 1}`}
                  onClick={() => toggle(id)}
                >
                  <img src={thumbUrl(id)} alt="" />
                </button>
              ))}
            </div>
          )}
          <button className="primary" disabled={!prompt.trim() || !imageReady} onClick={start}>
            Generate
          </button>
          <p className="hint">
            {imageReady
              ? 'Uses Codex and your ChatGPT plan. The photos chosen here go to OpenAI, without EXIF or GPS. Faces come back as a close likeness, not an exact copy.'
              : 'AI images need the codex command. Run the album with scripts/album.py, not in Docker.'}
          </p>
          {problem && (
            <p className="problem" role="alert">
              {problem}
            </p>
          )}
          <Runs runs={runs} onOpen={onOpenOutput} />
        </div>
      </div>
    </main>
  )
}
