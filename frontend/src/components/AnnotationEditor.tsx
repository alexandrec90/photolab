import { useState } from 'react'

import type { AnnotationData, AnnotationView } from '../api/types'

/** The closed choices, mirroring `PhotoAnnotation`; an edit outside them is refused. */
export const CHOICES: Record<string, string[]> = {
  setting: ['indoor', 'outdoor', 'unclear'],
  time_of_day: ['dawn', 'day', 'golden_hour', 'dusk', 'night', 'unknown'],
  shot_type: ['close_up', 'portrait', 'half_body', 'full_body', 'wide', 'no_people'],
  camera_height: ['low', 'eye_level', 'high', 'unknown'],
  light_direction: ['left', 'right', 'front', 'back', 'above', 'diffuse', 'unknown'],
  light_quality: ['hard', 'soft', 'mixed', 'unknown'],
  color_temperature: ['warm', 'neutral', 'cool', 'mixed'],
}

const label = (name: string) => name.replaceAll('_', ' ')

export function changedFields(annotation: AnnotationView): string[] {
  return Object.keys(annotation.data).filter(
    (key) => JSON.stringify(annotation.data[key]) !== JSON.stringify(annotation.original[key]),
  )
}

export function parseList(text: string): string[] {
  return text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

interface Props {
  annotation: AnnotationView
  onSave: (fields: AnnotationData) => void
}

function Original({ value }: { value: unknown }) {
  const text = Array.isArray(value) ? value.join(', ') : String(value)
  return <span className="was">model said: {text}</span>
}

export function AnnotationEditor({ annotation, onSave }: Props) {
  const { data, original } = annotation
  const changed = new Set(changedFields(annotation))
  const [caption, setCaption] = useState(String(data.caption ?? ''))
  const [tags, setTags] = useState(((data.tags as string[]) ?? []).join(', '))

  return (
    <div className="fields">
      <label data-changed={changed.has('caption')}>
        <span>Caption</span>
        <textarea
          rows={3}
          value={caption}
          onChange={(event) => setCaption(event.target.value)}
          onBlur={() => caption !== data.caption && onSave({ caption })}
        />
        {changed.has('caption') && <Original value={original.caption} />}
      </label>

      <label data-changed={changed.has('tags')}>
        <span>Tags</span>
        <input
          value={tags}
          onChange={(event) => setTags(event.target.value)}
          onBlur={() => {
            const next = parseList(tags)
            if (JSON.stringify(next) !== JSON.stringify(data.tags)) onSave({ tags: next })
          }}
        />
        {changed.has('tags') && <Original value={original.tags} />}
      </label>

      <div className="choices">
        {Object.entries(CHOICES).map(([name, options]) => (
          <label key={name} data-changed={changed.has(name)}>
            <span>{label(name)}</span>
            <select
              value={String(data[name])}
              onChange={(event) => onSave({ [name]: event.target.value })}
            >
              {options.map((option) => (
                <option key={option} value={option}>
                  {label(option)}
                </option>
              ))}
            </select>
            {changed.has(name) && <Original value={original[name]} />}
          </label>
        ))}
      </div>

      <dl className="facts">
        <dt>People seen</dt>
        <dd>
          {String(data.person_count)}
          {(data.people_descriptions as string[])?.length
            ? `: ${(data.people_descriptions as string[]).join('; ')}`
            : ''}
        </dd>
        {(data.quality_issues as string[])?.length > 0 && (
          <>
            <dt>Quality</dt>
            <dd>{(data.quality_issues as string[]).join('; ')}</dd>
          </>
        )}
        <dt>Annotated by</dt>
        <dd>
          {annotation.model}, prompt v{annotation.prompt_version}
        </dd>
      </dl>
    </div>
  )
}
