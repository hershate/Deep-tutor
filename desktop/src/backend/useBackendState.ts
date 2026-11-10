import { useCallback, useEffect, useRef, useState } from 'react'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type AppSnapshot, type BackendError, type CollectionNamespace } from './client'
import { storageBackend } from './storage'
import { backupBackend } from './backup'
import { notesBackend } from './notes'

// 103#8：keyring 会话槽丢失须显式重登，与过期/吊销同走登录界面
const authenticationErrors = new Set(['invalid_session', 'session_expired', 'session_revoked', 'account_disabled', 'login_required', 'session_credentials_unavailable'])

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
  // 24#5：最新集合镜像——提交前从这里取集合，避免渲染闭包旧集合把
  // 同帧内前一次写入静默覆盖掉（revision 防护恰被已前进的版本号绕过）
  const collections = useRef<AppSnapshot>(emptySnapshot)
  const inflight = useRef<Partial<Record<CollectionNamespace, Promise<unknown>>>>({})

  const applySnapshot = useCallback((next: AppSnapshot, expectedGeneration = generation.current) => {
    if (!mounted.current || expectedGeneration !== generation.current) return
    if (next.workspaceId !== workspaceId.current) {
      workspaceId.current = next.workspaceId
      lastEventId.current = 0
    }
    if (next.lastEventId < lastEventId.current) return
    lastEventId.current = next.lastEventId
    versions.current = next.versions
    collections.current = next
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
        // 38#2：直接取全量快照——其 lastEventId 本身就是最新游标，原先先
        // 逐页翻完 eventsSince 再取快照，事件载荷全程丢弃、纯冗余 IPC，
        // 事件爆发后首次 sync 串行数十次往返表现为「卡几秒才刷新」
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
    // 24#6：闭包局部存活标志取代共享 ref 的判定——StrictMode 双挂载下共享
    // ref 会被第二轮 effect 立即置回 true，第一轮的异步回调误判存活，
    // 造成监听器泄漏与双 bootstrap（对齐 App.tsx/useAgent.ts 的同型写法）
    let disposed = false
    const alive = () => !disposed
    let unlisten: (() => void) | undefined
    let starting = false
    let activeStartup = -1
    let settled = false
    let timer: number | undefined
    let transientRetries = 0
    const fail = (error: unknown, expectedGeneration = generation.current) => {
      if (!alive() || generation.current !== expectedGeneration) return
      const code = (error as Partial<BackendError>)?.code || ''
      if (authenticationErrors.has(code)) {
        settled = true
        setStartupError('')
        setLoginRequired(true)
        setReady(true)
        return
      }
      // 24#1：database_busy 是瞬态读争用（备份导入大事务/流式写入高峰），
      // 不得把整个应用卸载成错误页——保留当前快照与界面，静默延迟重试；
      // 连续 5 次仍失败才视为持续故障进入错误页
      if (code === 'database_busy' && transientRetries < 5) {
        transientRetries += 1
        reportBackendError(error)
        window.setTimeout(() => {
          if (!alive() || settled || generation.current !== expectedGeneration) return
          requestSync()
        }, 1500)
        return
      }
      // 106#1：startup_not_ready 单独放宽——bootstrap 20s 轮询窗口耗尽时
      // 后端可能仍在做大库首迁/杀毒扫描下的慢初始化，落成常驻错误页后
      // 唯一回正通道是不可发现的「切走再切回窗口」。改为持续轮询启动面：
      // initializing 期间给出「仍在初始化」提示，就绪后自动重跑 start()，
      // 真正失败（overview.error）才进入常规错误页
      if (code === 'startup_not_ready') {
        settled = true
        setStartupError('本地后端仍在初始化（首次启动的大数据目录迁移或杀毒扫描可能较慢），正在继续等待……此页面会在就绪后自动恢复。')
        setReady(false)
        const poll = async () => {
          for (;;) {
            await new Promise(resolve => window.setTimeout(resolve, 1000))
            if (!alive() || generation.current !== expectedGeneration) return
            try {
              const overview = await storageBackend.overview()
              if (!alive() || generation.current !== expectedGeneration) return
              if (overview.error) {
                fail({ code: overview.error.code, message: overview.error.message }, expectedGeneration)
                return
              }
              if (overview.ready) {
                start()
                return
              }
            } catch {
              // 轮询本身失败（如正在换库）——下一轮继续，直到存活/代际变化退出
            }
          }
        }
        void poll()
        return
      }
      transientRetries = 0
      settled = true
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
        // 24#8：bootstrap 成功即落定——20s 超时只看管 bootstrap 本身，
        // 原实现要等 sync 追平才置 settled，临界误报会把整树切到错误页
        settled = true
        transientRetries = 0
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
        if (!alive() || generation.current !== requestedGeneration) return
        settled = true
        transientRetries = 0
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
    const requestSyncCoalesced = (event?: { kind: string }) => {
      // 108#3：草稿域事件不触发全量快照——编辑期每次 350ms 停顿的
      // save_draft 原先引发一轮 MB 级 getAppState + 整树重渲染，而快照
      // 对草稿路径只含 versions.drafts 计数，编辑器已从写回执拿到 revision
      if (event?.kind === 'draft.saved' || event?.kind === 'draft.deleted') return
      if (coalesceTimer !== undefined) window.clearTimeout(coalesceTimer)
      coalesceTimer = window.setTimeout(() => {
        coalesceTimer = undefined
        requestSync()
      }, 120)
    }
    // 92 号事件契约：六类事件（application.reset、backup.exported/imported、
    // content.exported、draft.saved/deleted）无任何专项监听——发起方 UI 直接
    // 消费命令返回值，本全局快照是其中四类的消费路径；draft.saved/deleted
    // 由 108#3 跳过（编辑器消费写回执，全量快照对草稿路径是纯浪费）
    void onBackendEvent(requestSyncCoalesced).then(stop => {
      if (alive()) {
        unlisten = stop
        start()
      } else stop()
    }).catch(fail)
    // 108#5：激活窗口原先 focus + visibilitychange 双路径各拉一次全量
    // 快照；隐藏时的拉取结果无人消费——隐藏直接跳过，聚焦路径 300ms 去重
    let lastSyncAt = 0
    const refreshVisible = () => {
      if (document.hidden) return
      const now = Date.now()
      if (now - lastSyncAt < 300) return
      lastSyncAt = now
      refresh()
    }
    window.addEventListener('focus', refreshVisible)
    document.addEventListener('visibilitychange', refreshVisible)
    return () => {
      disposed = true
      mounted.current = false
      if (timer !== undefined) window.clearTimeout(timer)
      if (coalesceTimer !== undefined) window.clearTimeout(coalesceTimer)
      unlisten?.()
      window.removeEventListener('focus', refreshVisible)
      document.removeEventListener('visibilitychange', refreshVisible)
    }
  }, [applySnapshot, sync])

  // 24#5：接受值或更新函数（以最新集合为基准计算）；同命名空间的写入按
  // 提交顺序排队——后到者等先到者落库后携带新版本号提交，既不丢同帧写入、
  // 也不再依赖"恰被绕过"的 revision 防护
  const replaceCollection = useCallback(async <T,>(namespace: CollectionNamespace, value: T | ((current: T) => T)) => {
    const previous = inflight.current[namespace]
    const requestedGeneration = generation.current
    const task = (async () => {
      if (previous) await previous.catch(() => {})
      const resolved = typeof value === 'function'
        ? (value as (current: T) => T)(collections.current[namespace] as T)
        : value
      try {
        const receipt = await backend.replaceCollection(namespace, versions.current[namespace] ?? 0, resolved)
        // 80#1：写回执只带版本游标——集合内容本地构造（写请求本身已携带
        // 全量新值），不再下载整包全集合快照；后续事件仍走去抖全量兜底
        applySnapshot({ ...collections.current, [namespace]: resolved, versions: receipt.versions, lastEventId: receipt.lastEventId, workspaceId: receipt.workspaceId } as AppSnapshot, requestedGeneration)
        return receipt
      } catch (error) {
        if ((error as Partial<BackendError>)?.code !== 'revision_conflict') throw error
        // 快速连续操作时后到者撞版本号（先到者已生效）：立即拉取最新快照同步界面，
        // 再以明确指引报错，用户基于最新数据重试一次即可成功
        const fresh = await backend.getAppState()
        applySnapshot(fresh, requestedGeneration)
        throw { code: 'revision_conflict', message: '内容刚被其他操作更新，已为你刷新到最新，请重试一次。' } satisfies Partial<BackendError>
      }
    })()
    inflight.current[namespace] = task
    try {
      return await task
    } finally {
      if (inflight.current[namespace] === task) delete inflight.current[namespace]
    }
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

  const importNotes = useCallback(async (path: string) => {
    const requestedGeneration = generation.current
    const result = await notesBackend.import(path)
    applySnapshot(result.state, requestedGeneration)
    return result.imported
  }, [applySnapshot])

  return { snapshot, ready, startupError, loginRequired, replaceCollection, resetApplication, importBackup, importNotes, sync }
}
