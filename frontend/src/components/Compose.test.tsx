import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '../api/client'
import type { PhotoSummary, Stats } from '../api/types'
import { Compose } from './Compose'

const photo = (id: number, caption: string): PhotoSummary => ({
  id,
  origin: 'library',
  path: `${id}.jpg`,
  width: 300,
  height: 200,
  taken_at: null,
  review_status: 'pending',
  people: [],
  caption,
  annotated: true,
  annotation_error: null,
})

describe('Compose', () => {
  beforeEach(() => {
    vi.spyOn(api, 'stats').mockResolvedValue({ stock_ready: false } as Stats)
    vi.spyOn(api, 'generations').mockResolvedValue([])
    vi.spyOn(api, 'photos').mockResolvedValue({
      total: 2,
      items: [photo(1, 'Me on a walk'), photo(2, 'An empty beach')],
    })
    vi.spyOn(api, 'advice').mockResolvedValue({
      light: 'right',
      flip: true,
      warnings: ['Light mismatch'],
    })
    vi.spyOn(api, 'composite').mockResolvedValue({} as never)
  })

  async function chooseBoth() {
    render(<Compose version={0} onChanged={vi.fn()} onOpenOutput={vi.fn()} />)
    const [person] = await screen.findAllByRole('button', { name: 'Me on a walk' })
    await userEvent.click(person)
    const scenes = await screen.findAllByRole('button', { name: 'An empty beach' })
    await userEvent.click(scenes[scenes.length - 1])
  }

  it('cannot generate until both photos are chosen', async () => {
    render(<Compose version={0} onChanged={vi.fn()} onOpenOutput={vi.fn()} />)

    expect(await screen.findByRole('button', { name: 'Generate' })).toBeDisabled()
  })

  it('applies the advice and shows its warnings', async () => {
    await chooseBoth()

    expect(await screen.findByText('Light mismatch')).toBeInTheDocument()
    expect(screen.getByLabelText('Light from')).toHaveValue('right')
    expect(screen.getByLabelText('Flip person')).toBeChecked()
  })

  it('sends the chosen photos and settings', async () => {
    await chooseBoth()
    await screen.findByText('Light mismatch')

    await userEvent.click(screen.getByRole('button', { name: 'Generate' }))

    await waitFor(() =>
      expect(api.composite).toHaveBeenCalledWith(
        1,
        2,
        expect.objectContaining({ light: 'right', flip: true }),
      ),
    )
  })

  it('disables stock with the reason when no key is set', async () => {
    render(<Compose version={0} onChanged={vi.fn()} onOpenOutput={vi.fn()} />)

    expect(await screen.findByRole('button', { name: 'Stock' })).toBeDisabled()
  })

  it('shows why a run failed', async () => {
    vi.spyOn(api, 'generations').mockResolvedValue([
      {
        id: 1,
        kind: 'composite',
        params: {},
        status: 'failed',
        error: 'the matte is empty',
        subject_id: 1,
        background_id: 2,
        output_id: null,
        created_at: '',
        finished_at: null,
      },
    ])
    render(<Compose version={0} onChanged={vi.fn()} onOpenOutput={vi.fn()} />)

    expect(await screen.findByText('the matte is empty')).toBeInTheDocument()
  })
})
