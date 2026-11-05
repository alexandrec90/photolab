import { useCallback, useEffect, useState } from 'react'

import { api } from './api/client'
import type { Stats } from './api/types'

const BUSY_POLL_MS = 1500
const IDLE_POLL_MS = 15000

export function isBusy(stats: Stats | null): boolean {
  return stats?.jobs.some((job) => job.running) ?? false
}

/** Album counters, refreshed quickly while a job is running and lazily otherwise. */
export function useStats() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [offline, setOffline] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setStats(await api.stats())
      setOffline(false)
    } catch {
      setOffline(true)
    }
  }, [])

  const busy = isBusy(stats)
  useEffect(() => {
    void refresh()
    const timer = setInterval(() => void refresh(), busy ? BUSY_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(timer)
  }, [busy, refresh])

  return { stats, offline, refresh }
}
