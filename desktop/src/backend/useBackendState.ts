import { useCallback, useEffect, useRef, useState } from 'react'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type AppSnapshot, type BackendError, type CollectionNamespace } from './client'
import { backupBackend } from './backup'

const authenticationErrors = new Set(['invalid_session', 'session_expired', 'session_revoked', 'account_disabled', 'login_required'])

const emptySnapshot: AppSnapshot = {
  tasks: [], notes: [], resources: [], projects: [],
  preferences: { proactive: true, hintsOnly: true, notifications: true, weeklyReport: true, highContrast: false, reducedMotion: false, largeText: false, theme: 'light' },
  versions: { tasks: 0, notes: 0, resources: 0, projects: 0, preferences: 0, drafts: 0 },
  lastEventId: 0,
  workspaceId: 0,
}

export function useBackendState() {
  const [snapshot, setSnapshot] = useState<AppSnapshot>(emptySnapshot)
  const [ready, setReady] = useState(false)
  const [startupError, setStartupError] = useState('')
  const [loginRequired, setLoginRequired] = useState(false)
  const mounted = useRef(true)
  const versions = useRef(emptySnapshot.versions)
  const lastEventId = useRef(0)
  const workspaceId = useRef(0)
  const generation = useRef(0)
  const syncing = useRef<Promise<void> | null>(null)
  const queued = useRef(false)

  const applySnapshot = useCallback((next: AppSnapshot, expectedGeneration = generation.current) => {
    if (!mounted.current || expectedGeneration !== generation.current) return
    if (next.workspaceId !== workspaceId.current) {
      workspaceId.current = next.workspaceId
      lastEventId.current = 0
    }
    if (next.lastEventId < lastEventId.current) return
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
    const requestedGeneration = generation.current
    const task = (async () => {
      do {
        queued.current = false
        let page = await backend.eventsSince(lastEventId.current)
        while (page.hasMore) {
          const last = page.events.at(-1)
          if (!last) throw new Error('invalid_event_page')
          page = await backend.eventsSince(last.id)
        }
        applySnapshot(await backend.getAppState(), requestedGeneration)
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
    const requestSync = (event?: { kind: string }) => {
      if (event?.kind === 'security.session_started') {
        setLoginRequired(false)
        generation.current += 1
        lastEventId.current = 0
        versions.current = emptySnapshot.versions
        queued.current = true
        setSnapshot(emptySnapshot)
        setReady(false)
      }
      const requestedGeneration = generation.current
      void sync().then(() => {
        if (mounted.current && generation.current === requestedGeneration) setReady(true)
      }).catch(reportBackendError)
    }
    const refresh = () => requestSync()
    void onBackendEvent(requestSync).then(stop => {
      if (mounted.current) unlisten = stop
      else stop()
    }).catch(error => {
      reportBackendError(error)
      if (mounted.current) setStartupError(backendErrorMessage(error))
    })
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    void backend.bootstrap().then(state => {
      applySnapshot(state)
      return sync()
    }).then(() => { if (mounted.current) setReady(true) }).catch(error => {
      if (authenticationErrors.has((error as Partial<BackendError>)?.code || '')) {
        if (mounted.current) {
          setLoginRequired(true)
          setReady(true)
        }
        return
      }
      reportBackendError(error)
      if (mounted.current) setStartupError(backendErrorMessage(error))
    })
    return () => {
      mounted.current = false
      unlisten?.()
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [applySnapshot, sync])

  const replaceCollection = useCallback(async <T,>(namespace: CollectionNamespace, value: T) => {
    const requestedGeneration = generation.current
    const next = await backend.replaceCollection(namespace, versions.current[namespace] ?? 0, value)
    applySnapshot(next, requestedGeneration)
    return next
  }, [applySnapshot])

  const resetApplication = useCallback(async () => {
    const requestedGeneration = generation.current
    const next = await backend.resetApplication()
    applySnapshot(next, requestedGeneration)
    return next
  }, [applySnapshot])

  const importBackup = useCallback(async (path: string) => {
    const requestedGeneration = generation.current
    const next = await backupBackend.import(path)
    applySnapshot(next, requestedGeneration)
    return next
  }, [applySnapshot])

  return { snapshot, ready, startupError, loginRequired, replaceCollection, resetApplication, importBackup, sync }
}
