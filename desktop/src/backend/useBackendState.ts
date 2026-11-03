import { useCallback, useEffect, useRef, useState } from 'react'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type AppSnapshot, type CollectionNamespace } from './client'
import { backupBackend } from './backup'

const emptySnapshot: AppSnapshot = {
  tasks: [], notes: [], resources: [], projects: [],
  preferences: { proactive: true, hintsOnly: true, notifications: true, weeklyReport: true, highContrast: false, reducedMotion: false, largeText: false, theme: 'light' },
  versions: { tasks: 0, notes: 0, resources: 0, projects: 0, preferences: 0, drafts: 0 },
  lastEventId: 0,
}

export function useBackendState() {
  const [snapshot, setSnapshot] = useState<AppSnapshot>(emptySnapshot)
  const [ready, setReady] = useState(false)
  const [startupError, setStartupError] = useState('')
  const mounted = useRef(true)
  const versions = useRef(emptySnapshot.versions)
  const lastEventId = useRef(0)
  const syncing = useRef<Promise<void> | null>(null)
  const queued = useRef(false)

  const applySnapshot = useCallback((next: AppSnapshot) => {
    if (!mounted.current || next.lastEventId < lastEventId.current) return
    lastEventId.current = next.lastEventId
    versions.current = next.versions
    setSnapshot(next)
  }, [])

  const sync = useCallback(() => {
    if (!mounted.current) return Promise.resolve()
    if (syncing.current) {
      queued.current = true
      return syncing.current
    }
    const task = (async () => {
      do {
        queued.current = false
        let page = await backend.eventsSince(lastEventId.current)
        while (page.hasMore) {
          const last = page.events.at(-1)
          if (!last) throw new Error('invalid_event_page')
          page = await backend.eventsSince(last.id)
        }
        applySnapshot(await backend.getAppState())
      } while (queued.current && mounted.current)
    })()
    syncing.current = task
    void task.finally(() => {
      syncing.current = null
      if (queued.current && mounted.current) void sync().catch(reportBackendError)
    }).catch(() => {})
    return task
  }, [applySnapshot])

  useEffect(() => {
    mounted.current = true
    let unlisten: (() => void) | undefined
    const requestSync = () => { void sync().catch(reportBackendError) }
    void onBackendEvent(requestSync).then(stop => {
      if (mounted.current) unlisten = stop
      else stop()
    }).catch(error => {
      reportBackendError(error)
      if (mounted.current) setStartupError(backendErrorMessage(error))
    })
    window.addEventListener('focus', requestSync)
    document.addEventListener('visibilitychange', requestSync)
    void backend.bootstrap().then(state => {
      applySnapshot(state)
      return sync()
    }).then(() => { if (mounted.current) setReady(true) }).catch(error => {
      reportBackendError(error)
      if (mounted.current) setStartupError(backendErrorMessage(error))
    })
    return () => {
      mounted.current = false
      unlisten?.()
      window.removeEventListener('focus', requestSync)
      document.removeEventListener('visibilitychange', requestSync)
    }
  }, [applySnapshot, sync])

  const replaceCollection = useCallback(async <T,>(namespace: CollectionNamespace, value: T) => {
    const next = await backend.replaceCollection(namespace, versions.current[namespace] ?? 0, value)
    applySnapshot(next)
    return next
  }, [applySnapshot])

  const resetApplication = useCallback(async () => {
    const next = await backend.resetApplication()
    applySnapshot(next)
    return next
  }, [applySnapshot])

  const importBackup = useCallback(async (path: string) => {
    const next = await backupBackend.import(path)
    applySnapshot(next)
    return next
  }, [applySnapshot])

  return { snapshot, ready, startupError, replaceCollection, resetApplication, importBackup, sync }
}
