import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { AnnotationView } from '../api/types'
import { AnnotationEditor, changedFields, parseList } from './AnnotationEditor'

const base = {
  caption: 'A beach',
  tags: ['beach'],
  setting: 'outdoor',
  time_of_day: 'day',
  shot_type: 'wide',
  camera_height: 'eye_level',
  light_direction: 'left',
  light_quality: 'hard',
  color_temperature: 'warm',
  person_count: 0,
  people_descriptions: [],
  quality_issues: [],
}

const annotation = (data = base): AnnotationView => ({
  data,
  original: base,
  model: 'claude-opus-5-5',
  prompt_version: 1,
  updated_at: '2026-01-01T00:00:00Z',
})

describe('parseList', () => {
  it('splits and trims and drops blanks', () => {
    expect(parseList(' a, b ,, c ')).toEqual(['a', 'b', 'c'])
  })
})

describe('changedFields', () => {
  it('finds what a human changed', () => {
    expect(changedFields(annotation({ ...base, caption: 'Mine' }))).toEqual(['caption'])
  })
})

describe('AnnotationEditor', () => {
  it('saves a caption when focus leaves it, and only if it changed', async () => {
    const onSave = vi.fn()
    render(<AnnotationEditor annotation={annotation()} onSave={onSave} />)
    const caption = screen.getByLabelText('Caption')

    await userEvent.click(caption)
    await userEvent.tab()
    expect(onSave).not.toHaveBeenCalled()

    await userEvent.type(caption, ' at noon')
    await userEvent.tab()
    expect(onSave).toHaveBeenCalledWith({ caption: 'A beach at noon' })
  })

  it('saves a corrected choice at once', async () => {
    const onSave = vi.fn()
    render(<AnnotationEditor annotation={annotation()} onSave={onSave} />)

    await userEvent.selectOptions(screen.getByLabelText('light direction'), 'right')

    expect(onSave).toHaveBeenCalledWith({ light_direction: 'right' })
  })

  it('shows what the model said beside a correction', () => {
    render(
      <AnnotationEditor annotation={annotation({ ...base, setting: 'indoor' })} onSave={vi.fn()} />,
    )

    expect(screen.getByText('model said: outdoor')).toBeInTheDocument()
  })
})
