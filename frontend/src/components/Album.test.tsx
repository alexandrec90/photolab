import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { api } from '../api/client'
import type { PhotoSummary } from '../api/types'
import { Album } from './Album'

const photo: PhotoSummary = {
  id: 1,
  origin: 'library',
  path: 'trip/beach.jpg',
  width: 300,
  height: 200,
  taken_at: null,
  review_status: 'pending',
  people: [],
  caption: 'A beach at sunset',
  annotated: true,
  annotation_error: null,
}

describe('Album', () => {
  it('lists photos and opens one', async () => {
    vi.spyOn(api, 'photos').mockResolvedValue({ total: 1, items: [photo] })
    const onOpen = vi.fn()
    render(<Album query={{}} title="All photos" version={0} onOpen={onOpen} />)

    await userEvent.click(await screen.findByRole('button', { name: 'A beach at sunset' }))

    expect(onOpen).toHaveBeenCalledWith(photo, [photo])
  })

  it('says so when a shelf is empty', async () => {
    vi.spyOn(api, 'photos').mockResolvedValue({ total: 0, items: [] })
    render(<Album query={{}} title="Rejected" version={0} onOpen={vi.fn()} />)

    expect(await screen.findByText('Nothing here yet.')).toBeInTheDocument()
  })

  it('searches what was typed', async () => {
    const photos = vi.spyOn(api, 'photos').mockResolvedValue({ total: 0, items: [] })
    render(<Album query={{ review: 'pending' }} title="To review" version={0} onOpen={vi.fn()} />)

    await userEvent.type(screen.getByLabelText('Search photos'), 'beach')

    await waitFor(() =>
      expect(photos).toHaveBeenLastCalledWith({ review: 'pending', q: 'beach', limit: 60 }),
    )
  })

  it('offers more when the page is short of the total', async () => {
    const photos = vi.spyOn(api, 'photos').mockResolvedValue({ total: 2, items: [photo] })
    render(<Album query={{}} title="All" version={0} onOpen={vi.fn()} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Show more' }))

    expect(photos).toHaveBeenLastCalledWith({ q: '', limit: 60, offset: 1 })
    // The mock served the same photo again, as a shifted offset does: it appears once.
    expect(screen.getAllByRole('button', { name: 'A beach at sunset' })).toHaveLength(1)
  })

  it('shows a failure rather than an empty album', async () => {
    vi.spyOn(api, 'photos').mockRejectedValue(new Error('database is down'))
    render(<Album query={{}} title="All" version={0} onOpen={vi.fn()} />)

    expect(await screen.findByRole('alert')).toHaveTextContent('database is down')
  })
})
