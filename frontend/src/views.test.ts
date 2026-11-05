import { describe, expect, it } from 'vitest'

import type { Stats } from './api/types'
import { isBusy } from './useStats'
import { VIEWS } from './views'

const stats = { photos: 3, outputs: 2 } as Stats

describe('VIEWS', () => {
  it('have unique ids', () => {
    expect(new Set(VIEWS.map((view) => view.id)).size).toBe(VIEWS.length)
  })

  it('count originals and outputs together under all photos', () => {
    expect(VIEWS[0].count(stats)).toBe(5)
  })
})

describe('isBusy', () => {
  it('follows the running jobs', () => {
    const running = { ...stats, jobs: [{ running: true }] } as Stats
    const idle = { ...stats, jobs: [{ running: false }] } as Stats

    expect(isBusy(running)).toBe(true)
    expect(isBusy(idle)).toBe(false)
    expect(isBusy(null)).toBe(false)
  })
})
