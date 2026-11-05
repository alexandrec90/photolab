import { useEffect, useState } from 'react'

import { api, thumbUrl } from '../api/client'
import type { Advice, CompositeSettings, Light } from '../api/types'
import { PhotoPicker } from './PhotoPicker'
import { Runs, useRuns } from './Runs'

const DEFAULTS: CompositeSettings = {
  center_x: 0.5,
  baseline_y: 0.92,
  height: 0.6,
  flip: false,
  color_match: 0.6,
  shadow_strength: 0.6,
  light_wrap: 0.3,
  light: 'diffuse',
}

const SLIDERS: [keyof CompositeSettings, string, number, number][] = [
  ['center_x', 'Across', 0, 1],
  ['baseline_y', 'Feet at', 0.3, 1.3],
  ['height', 'Height', 0.1, 1.5],
  ['color_match', 'Colour match', 0, 1],
  ['shadow_strength', 'Shadow', 0, 1],
  ['light_wrap', 'Light wrap', 0, 1],
]

const LIGHTS: Light[] = ['diffuse', 'left', 'right', 'front', 'back', 'above']

interface Props {
  version: number
  onChanged: () => void
  onOpenOutput: (id: number) => void
}

function Preview({ background, settings }: { background: number; settings: CompositeSettings }) {
  const style = {
    left: `${settings.center_x * 100}%`,
    bottom: `${(1 - settings.baseline_y) * 100}%`,
    height: `${settings.height * 100}%`,
  }
  return (
    <figure className="preview">
      <img src={thumbUrl(background)} alt="Background" />
      <span className="placement" style={style} />
    </figure>
  )
}

export function Compose({ version, onChanged, onOpenOutput }: Props) {
  const [subject, setSubject] = useState<number | null>(null)
  const [background, setBackground] = useState<number | null>(null)
  const [settings, setSettings] = useState(DEFAULTS)
  const [advice, setAdvice] = useState<Advice | null>(null)
  const [stockReady, setStockReady] = useState(false)
  const [problem, setProblem] = useState('')
  const runs = useRuns('composite', version)

  useEffect(() => {
    void api.stats().then((stats) => setStockReady(stats.stock_ready))
  }, [])

  useEffect(() => {
    if (subject === null || background === null) return
    let current = true
    void api.advice(subject, background).then((found) => {
      if (!current) return
      setAdvice(found)
      setSettings((now) => ({ ...now, light: found.light, flip: found.flip }))
    })
    return () => {
      current = false
    }
  }, [subject, background])

  async function start() {
    if (subject === null || background === null) return
    try {
      await api.composite(subject, background, settings)
      setProblem('')
      onChanged()
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error))
    }
  }

  const set = (name: keyof CompositeSettings, value: number | boolean | Light) =>
    setSettings({ ...settings, [name]: value })

  return (
    <main className="compose">
      <header className="album-head">
        <h2>Compose</h2>
      </header>
      <div className="compose-body">
        <div className="compose-pick">
          <PhotoPicker title="Person" selected={subject} onSelect={setSubject} />
          <PhotoPicker
            title="Scene"
            selected={background}
            onSelect={setBackground}
            allowStock
            stockReady={stockReady}
          />
        </div>
        <div className="compose-controls">
          {background !== null ? (
            <Preview background={background} settings={settings} />
          ) : (
            <p className="empty">Pick a person and a scene.</p>
          )}
          {advice?.warnings.map((warning) => (
            <p key={warning} className="warning">
              {warning}
            </p>
          ))}
          <div className="sliders">
            {SLIDERS.map(([name, label, min, max]) => (
              <label key={name}>
                <span>{label}</span>
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={0.01}
                  value={settings[name] as number}
                  onChange={(event) => set(name, Number(event.target.value))}
                />
              </label>
            ))}
            <label>
              <span>Light from</span>
              <select
                value={settings.light}
                onChange={(event) => set('light', event.target.value as Light)}
              >
                {LIGHTS.map((light) => (
                  <option key={light}>{light}</option>
                ))}
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settings.flip}
                onChange={(event) => set('flip', event.target.checked)}
              />
              <span>Flip person</span>
            </label>
          </div>
          <button className="primary" disabled={!subject || !background} onClick={start}>
            Generate
          </button>
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
