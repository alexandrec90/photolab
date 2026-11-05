import { useCallback, useState } from 'react'

import { api } from './api/client'
import type { PhotoSummary } from './api/types'
import { Album } from './components/Album'
import { Compose } from './components/Compose'
import { Imagine } from './components/Imagine'
import { Sidebar } from './components/Sidebar'
import { Viewer } from './components/Viewer'
import { useStats } from './useStats'
import { VIEWS } from './views'

interface Open {
  photo: PhotoSummary
  listing: PhotoSummary[]
}

export function App() {
  const { stats, offline, refresh } = useStats()
  const [viewId, setViewId] = useState('all')
  const [version, setVersion] = useState(0)
  const [open, setOpen] = useState<Open | null>(null)

  const changed = useCallback(() => {
    setVersion((now) => now + 1)
    void refresh()
  }, [refresh])

  const openById = useCallback(async (id: number) => {
    const photo = await api.photo(id)
    setOpen({ photo, listing: [photo] })
  }, [])

  const view = VIEWS.find((item) => item.id === viewId)

  return (
    <div className="app">
      <Sidebar
        stats={stats}
        offline={offline}
        active={viewId}
        onSelect={setViewId}
        onChanged={changed}
      />
      {view ? (
        <Album
          query={view.query}
          title={view.label}
          version={version}
          onOpen={(photo, listing) => setOpen({ photo, listing })}
        />
      ) : viewId === 'imagine' ? (
        <Imagine
          version={version}
          imageReady={stats?.image_ready ?? false}
          onChanged={changed}
          onOpenOutput={openById}
        />
      ) : (
        <Compose version={version} onChanged={changed} onOpenOutput={openById} />
      )}
      {open && (
        <Viewer
          photo={open.photo}
          listing={open.listing}
          onNavigate={(photo) => setOpen({ ...open, photo })}
          onClose={() => setOpen(null)}
          onChanged={changed}
        />
      )}
    </div>
  )
}
