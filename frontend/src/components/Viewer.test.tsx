import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api/client'
import type { PhotoDetail, PhotoSummary } from '../api/types'
import { Viewer } from './Viewer'

const summary = (id: number): PhotoSummary => ({
  id,
  origin: 'library',
  path: `${id}.jpg`,
  width: 300,
  height: 200,
  taken_at: null,
  review_status: 'pending',
  people: [],
  caption: null,
  annotated: false,
  annotation_error: null,
})

const detail: PhotoDetail = {
  ...summary(1),
  format: 'JPEG',
  exif: {},
  source: {},
  annotation: null,
  made_by: null,
}

const first = summary(1)
const second = summary(2)

function open(overrides = {}) {
  const props = {
    photo: first,
    listing: [first, second],
    onNavigate: vi.fn(),
    onClose: vi.fn(),
    onChanged: vi.fn(),
    ...overrides,
  }
  render(<Viewer {...props} />)
  return props
}

describe('Viewer', () => {
  beforeEach(() => {
    vi.spyOn(api, 'photo').mockResolvedValue(detail)
    vi.spyOn(api, 'editPhoto').mockResolvedValue({ ...detail, review_status: 'approved' })
  })

  it('approves with a key and moves on, which is what makes an audit fast', async () => {
    const props = open()
    await screen.findByText('Not annotated yet.')

    fireEvent.keyDown(window, { key: 'a' })

    await waitFor(() => expect(api.editPhoto).toHaveBeenCalledWith(1, { review_status: 'approved' }))
    await waitFor(() => expect(props.onNavigate).toHaveBeenCalledWith(second))
    expect(props.onChanged).toHaveBeenCalled()
  })

  it('does not treat typing a name as a verdict', async () => {
    open()
    const people = await screen.findByLabelText('People in this photo')

    await userEvent.type(people, 'Sarah')

    expect(api.editPhoto).not.toHaveBeenCalled()
  })

  it('saves named people when focus leaves', async () => {
    open()

    await userEvent.type(await screen.findByLabelText('People in this photo'), 'Alex, Sam')
    await userEvent.tab()

    await waitFor(() =>
      expect(api.editPhoto).toHaveBeenCalledWith(1, { people: ['Alex', 'Sam'] }),
    )
  })

  it('closes on Escape, and steps with the arrows but not past the end', async () => {
    const props = open({ photo: second })
    await screen.findByText('Not annotated yet.')

    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(props.onNavigate).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(props.onNavigate).toHaveBeenCalledWith(first)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(props.onClose).toHaveBeenCalled()
  })

  it('queues a recipe and says where the result goes', async () => {
    vi.spyOn(api, 'enhance').mockResolvedValue({} as never)
    open()

    await userEvent.click(await screen.findByRole('button', { name: 'Film' }))

    expect(api.enhance).toHaveBeenCalledWith(1, 'film')
    expect(await screen.findByRole('status')).toHaveTextContent('Outputs')
  })

  it('queues a relight and says where the photo goes', async () => {
    vi.spyOn(api, 'relight').mockResolvedValue({} as never)
    open()

    await userEvent.click(await screen.findByRole('button', { name: 'Relight with AI' }))

    expect(api.relight).toHaveBeenCalledWith(1)
    expect(await screen.findByRole('status')).toHaveTextContent('OpenAI')
  })

  it('says why a relight could not start', async () => {
    vi.spyOn(api, 'relight').mockRejectedValue(new Error('AI images need the codex command'))
    open()

    await userEvent.click(await screen.findByRole('button', { name: 'Relight with AI' }))

    expect(await screen.findByRole('status')).toHaveTextContent('codex command')
  })

  it('records the words an AI image was made from, and every reference', async () => {
    vi.spyOn(api, 'photo').mockResolvedValue({
      ...detail,
      origin: 'generated',
      made_by: {
        id: 4,
        kind: 'ai',
        params: { prompt: 'a pier in fog', preset: 'free', reference_ids: [5, 6, 7] },
        status: 'done',
        error: null,
        subject_id: 5,
        background_id: 6,
        output_id: 1,
        created_at: '',
        finished_at: '',
      },
    })
    open()

    expect(await screen.findByText('AI image: a pier in fog')).toBeInTheDocument()
    expect(screen.getByAltText('Input photo 7')).toBeInTheDocument()
  })

  it('labels a relight as AI work', async () => {
    vi.spyOn(api, 'photo').mockResolvedValue({
      ...detail,
      origin: 'generated',
      made_by: {
        id: 4,
        kind: 'ai',
        params: { prompt: '', preset: 'relight', reference_ids: [5] },
        status: 'done',
        error: null,
        subject_id: 5,
        background_id: null,
        output_id: 1,
        created_at: '',
        finished_at: '',
      },
    })
    open()

    expect(await screen.findByText('AI image: relit by Codex')).toBeInTheDocument()
  })

  it('shows why an annotation failed', async () => {
    vi.spyOn(api, 'photo').mockResolvedValue({ ...detail, annotation_error: 'the model declined' })
    open()

    expect(await screen.findByText('the model declined')).toBeInTheDocument()
  })

  it("credits a stock photo's photographer", async () => {
    vi.spyOn(api, 'photo').mockResolvedValue({
      ...detail,
      origin: 'stock',
      source: { photographer: 'Ana', page_url: 'https://www.pexels.com/photo/42/' },
    })
    open()

    expect(await screen.findByRole('link', { name: 'Ana' })).toBeInTheDocument()
  })

  it('does not suggest an output is waiting for an annotation', async () => {
    vi.spyOn(api, 'photo').mockResolvedValue({ ...detail, origin: 'generated' })
    open()

    await screen.findByLabelText('People in this photo')

    expect(screen.queryByText('Not annotated yet.')).not.toBeInTheDocument()
  })

  it('shows what an output was made from', async () => {
    vi.spyOn(api, 'photo').mockResolvedValue({
      ...detail,
      origin: 'generated',
      made_by: {
        id: 3,
        kind: 'composite',
        params: { light: 'left' },
        status: 'done',
        error: null,
        subject_id: 4,
        background_id: 5,
        output_id: 1,
        created_at: '2026-01-01T00:00:00Z',
        finished_at: null,
      },
    })
    open()

    expect(await screen.findByAltText('Input photo 4')).toBeInTheDocument()
    expect(screen.getByAltText('Input photo 5')).toBeInTheDocument()
  })
})
