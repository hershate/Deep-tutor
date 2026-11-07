import { useCallback, useEffect, useRef, useState } from 'react'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type AppSnapshot, type BackendError, type CollectionNamespace } from './client'
import { backupBackend } from './backup'
import { notesBackend } from './notes'

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
    let starting = false
    let activeStartup = -1
    let settled = false
    let timer: number | undefined
    const fail = (error: unknown, expectedGeneration = generation.current) => {
      if (!mounted.current || generation.current !== expectedGeneration) return
      settled = true
      if (authenticationErrors.has((error as Partial<BackendError>)?.code || '')) {
        setStartupError('')
        setLoginRequired(true)
        setReady(true)
        return
      }
      reportBackendError(error)
      setStartupError(backendErrorMessage(error))
      setReady(false)
    }
    const armTimeout = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      settled = false
      timer = window.setTimeout(() => {
        if (!mounted.current || settled || !starting) return
        settled = true
        setStartupError('等待本地后端响应已超过 20 秒。请重试，或检查应用数据目录权限及其他知舟进程是否占用数据库。重试不会清除学习数据。')
        setReady(false)
      }, 20000)
    }
    const start = () => {
      const requestedGeneration = generation.current
      activeStartup = requestedGeneration
      starting = true
      setStartupError('')
      setReady(false)
      armTimeout()
      const bootstrap = async () => {
        const deadline = Date.now() + 20000
        for (;;) {
          try { return await backend.bootstrap() }
          catch (error) {
            const code = (error as Partial<BackendError>)?.code
            if (code !== 'startup_not_ready' || Date.now() >= deadline || !mounted.current || generation.current !== requestedGeneration) throw error
            await new Promise(resolve => window.setTimeout(resolve, 250))
          }
        }
      }
      void bootstrap().then(state => {
        applySnapshot(state, requestedGeneration)
        return sync()
      }).then(() => {
        if (!mounted.current || generation.current !== requestedGeneration || activeStartup !== requestedGeneration) return
        starting = false
        settled = true
        setStartupError('')
        setLoginRequired(false)
        setReady(true)
      }).catch(error => {
        if (activeStartup === requestedGeneration) starting = false
        fail(error, requestedGeneration)
      })
    }
    const requestSync = (event?: { kind: string }) => {
      if (event?.kind === 'security.session_started') {
        setLoginRequired(false)
        generation.current += 1
        lastEventId.current = 0
        versions.current = emptySnapshot.versions
        queued.current = true
        setSnapshot(emptySnapshot)
        start()
        return
      }
      if (starting) return
      const requestedGeneration = generation.current
      void sync().then(() => {
        if (!mounted.current || generation.current !== requestedGeneration) return
        settled = true
        setStartupError('')
        setLoginRequired(false)
        setReady(true)
      }).catch(error => fail(error, requestedGeneration))
    }
    const refresh = () => requestSync()
    // 14#9：事件密集期（讲义逐单元、agent 逐步输出）合并刷新——
    // 120ms 尾沿去抖把“每事件一次全量快照+全树 reconcile”压缩为
    // 每次事件簇一次；sync 内部队列仍保证不丢事件
    let coalesceTimer: number | undefined
    const requestSyncCoalesced = () => {
      if (coalesceTimer !== undefined) window.clearTimeout(coalesceTimer)
      coalesceTimer = window.setTimeout(() => {
        coalesceTimer = undefined
        requestSync()
      }, 120)
    }
    void onBackendEvent(requestSyncCoalesced).then(stop => {
      if (mounted.current) {
        unlisten = stop
        start()
      } else stop()
    }).catch(fail)
    window.addEventListener('focus', refresh)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      mounted.current = false
      if (timer !== undefined) window.clearTimeout(timer)
      if (coalesceTimer !== undefined) window.clearTimeout(coalesceTimer)
      unlisten?.()
      window.removeEventListener('focus', refresh)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [applySnapshot, sync])

  const replaceCollection = useCallback(async <T,>(namespace: CollectionNamespace, value: T) => {
    const requestedGeneration = generation.current
    try {
      const next = await backend.replaceCollection(namespace, versions.current[namespace] ?? 0, value)
      applySnapshot(next, requestedGeneration)
      return next
    } catch (error) {
      if ((error as Partial<BackendError>)?.code !== 'revision_conflict') throw error
      // 快速连续操作时后到者撞版本号（先到者已生效）：立即拉取最新快照同步界面，
      // 再以明确指引报错，用户基于最新数据重试一次即可成功
      const fresh = await backend.getAppState()
      applySnapshot(fresh, requestedGeneration)
      throw { code: 'revision_conflict', message: '内容刚被其他操作更新，已为你刷新到最新，请重试一次。' } satisfies Partial<BackendError>
    }
  }, [applySnapshot, sync])

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

  const importNotes = useCallback(async (path: string) => {
    const requestedGeneration = generation.current
    const result = await notesBackend.import(path)
    applySnapshot(result.state, requestedGeneration)
    return result.imported
  }, [applySnapshot])

  return { snapshot, ready, startupError, loginRequired, replaceCollection, resetApplication, importBackup, importNotes, sync }
}
