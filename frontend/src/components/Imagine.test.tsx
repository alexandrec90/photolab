import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../api/client'
import type { Generation, PhotoPage, PhotoSummary } from '../api/types'
import { Imagine } from './Imagine'

const photo = (id: number): PhotoSummary => ({
  id,
  origin: 'library',
  path: `${id}.jpg`,
  width: 300,
  height: 200,
  taken_at: null,
  review_status: 'pending',
  people: [],
  caption: `Photo ${id}`,
  annotated: true,
  annotation_error: null,
})

const page: PhotoPage = { total: 2, items: [photo(1), photo(2)] }

function open(imageReady = true) {
  const props = { version: 0, imageReady, onChanged: vi.fn(), onOpenOutput: vi.fn() }
  render(<Imagine {...props} />)
  return props
}

const typePrompt = () =>
  userEvent.type(screen.getByLabelText('What should it show?'), 'Me on a beach')

describe('Imagine', () => {
  beforeEach(() => {
    vi.spyOn(api, 'photos').mockResolvedValue(page)
    vi.spyOn(api, 'generations').mockResolvedValue([])
  })

  it('needs words before it can start', () => {
    open()

    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Find photos/ })).toBeDisabled()
  })

  it('sends the words and the chosen photos', async () => {
    vi.spyOn(api, 'imagine').mockResolvedValue({} as Generation)
    const props = open()
    await typePrompt()

    await userEvent.click(await screen.findByRole('button', { name: 'Photo 2' }))
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }))

    expect(api.imagine).toHaveBeenCalledWith('Me on a beach', [2])
    expect(props.onChanged).toHaveBeenCalled()
  })

  it('picking a photo twice takes it back out', async () => {
    vi.spyOn(api, 'imagine').mockResolvedValue({} as Generation)
    open()
    await typePrompt()
    const pick = await screen.findByRole('button', { name: 'Photo 1' })

    await userEvent.click(pick)
    expect(pick).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(pick)
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }))

    expect(api.imagine).toHaveBeenCalledWith('Me on a beach', [])
  })

  it('takes a chosen photo back out from the shortlist', async () => {
    open()
    await userEvent.click(await screen.findByRole('button', { name: 'Photo 1' }))

    await userEvent.click(screen.getByRole('button', { name: 'Remove photo 1' }))

    expect(screen.queryByRole('button', { name: 'Remove photo 1' })).not.toBeInTheDocument()
  })

  it('fills the shortlist from the catalog and says why', async () => {
    vi.spyOn(api, 'suggest').mockResolvedValue({ reference_ids: [1, 2], why: 'Clear shots.' })
    open()
    await typePrompt()

    await userEvent.click(screen.getByRole('button', { name: /Find photos/ }))

    expect(await screen.findByText('Clear shots.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove photo 2' })).toBeInTheDocument()
    expect(api.suggest).toHaveBeenCalledWith('Me on a beach')
  })

  it('shows a failure, and can look again afterwards', async () => {
    vi.spyOn(api, 'suggest').mockRejectedValue(new ApiError('You hit your usage limit'))
    open()
    await typePrompt()

    await userEvent.click(screen.getByRole('button', { name: /Find photos/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent('usage limit')
    await waitFor(() => expect(screen.getByRole('button', { name: /Find photos/ })).toBeEnabled())
  })

  it('says what to do when Codex is out of reach', async () => {
    open(false)
    await typePrompt()

    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
    expect(screen.getByText(/scripts\/album\.py/)).toBeInTheDocument()
  })

  it('warns where the photos go, and that a face is a likeness', () => {
    open()

    expect(screen.getByText(/go to OpenAI/)).toHaveTextContent('close likeness')
  })

  it('lists only AI runs', async () => {
    vi.spyOn(api, 'generations').mockResolvedValue([
      { id: 1, kind: 'ai', status: 'running', error: null, output_id: null },
      { id: 2, kind: 'composite', status: 'running', error: 'not this one', output_id: null },
    ] as Generation[])
    open()

    expect(await screen.findByText('running…')).toBeInTheDocument()
    expect(screen.queryByText('not this one')).not.toBeInTheDocument()
  })
})
