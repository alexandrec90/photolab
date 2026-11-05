import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApiError, api } from '../api/client'
import type { Stats } from '../api/types'
import { Sidebar } from './Sidebar'

const stats: Stats = {
  photos: 12,
  outputs: 1,
  annotated: 5,
  unannotated: 7,
  failed: 0,
  to_review: 5,
  approved: 0,
  rejected: 0,
  annotation_backends: ['codex', 'claude'],
  annotation_backend: 'codex',
  annotation_model: 'gpt-6-luna',
  annotation_ready: true,
  image_ready: true,
  stock_ready: false,
  jobs: [],
}

function show(overrides: Partial<Stats> = {}) {
  const props = {
    stats: { ...stats, ...overrides },
    offline: false,
    active: 'all',
    onSelect: vi.fn(),
    onChanged: vi.fn(),
  }
  render(<Sidebar {...props} />)
  return props
}

describe('Sidebar', () => {
  it('counts each shelf and switches between them', async () => {
    const props = show()

    await userEvent.click(screen.getByRole('button', { name: /To review/ }))

    expect(screen.getByRole('button', { name: /All photos/ })).toHaveTextContent('13')
    expect(props.onSelect).toHaveBeenCalledWith('review')
  })

  it('says how many photos an annotation run will send', () => {
    show()

    const button = screen.getByRole('button', { name: /Annotate 7/ })

    expect(button).toHaveAttribute('title', 'Sends 7 photos to codex')
  })

  it('runs on the configured backend unless another is picked', async () => {
    const annotate = vi.spyOn(api, 'annotate').mockResolvedValue({})
    show()

    await userEvent.click(screen.getByRole('button', { name: /^Annotate/ }))
    expect(annotate).toHaveBeenLastCalledWith(undefined, 'codex')

    await userEvent.selectOptions(screen.getByLabelText('Annotate with'), 'claude')
    await userEvent.click(screen.getByRole('button', { name: /^Annotate/ }))
    expect(annotate).toHaveBeenLastCalledWith(undefined, 'claude')
  })

  it('tries a small sample first, so that quality can be audited', async () => {
    const annotate = vi.spyOn(api, 'annotate').mockResolvedValue({})
    show()

    await userEvent.click(screen.getByRole('button', { name: 'Try 20' }))

    expect(annotate).toHaveBeenCalledWith(20, 'codex')
  })

  it('offers no choice when there is only one backend', () => {
    show({ annotation_backends: ['api'], annotation_backend: 'api' })

    expect(screen.queryByLabelText('Annotate with')).not.toBeInTheDocument()
  })

  it('switches to the Imagine view', async () => {
    const props = show()

    await userEvent.click(screen.getByRole('button', { name: 'Imagine' }))

    expect(props.onSelect).toHaveBeenCalledWith('imagine')
  })

  it('has nothing to annotate when everything is done', () => {
    show({ unannotated: 0 })

    expect(screen.getByRole('button', { name: /^Annotate/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: /Try 20/ })).toBeDisabled()
  })

  it('explains what annotating needs', async () => {
    vi.spyOn(api, 'annotate').mockRejectedValue(new ApiError('codex cannot annotate here'))
    show({ annotation_ready: false, annotation_backends: [] })

    await userEvent.click(screen.getByRole('button', { name: /^Annotate/ }))

    expect(screen.getByText(/Sign in to the codex or claude command/)).toBeInTheDocument()
    expect(await screen.findByRole('alert')).toHaveTextContent('codex cannot annotate here')
  })

  it('shows a running job and blocks starting it twice', () => {
    show({
      jobs: [{ name: 'scan', running: true, done: 3, failed: 0, total: 9, message: '' }],
    })

    expect(screen.getByText('running 3/9')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scan library' })).toBeDisabled()
  })

  it('refreshes after starting a scan', async () => {
    const scan = vi.spyOn(api, 'scan').mockResolvedValue({})
    const props = show()

    await userEvent.click(screen.getByRole('button', { name: 'Scan library' }))

    expect(scan).toHaveBeenCalled()
    await waitFor(() => expect(props.onChanged).toHaveBeenCalled())
  })

  it('says when the API cannot be reached', () => {
    render(
      <Sidebar stats={null} offline active="all" onSelect={vi.fn()} onChanged={vi.fn()} />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('not reachable')
  })
})
