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
  // 117 轮 C1：恢复轮询的就绪分支读取当前 ready——非轮询路径（窗口聚焦
  // 触发 requestSync 成功、超时后 bootstrap 迟到成功）先行恢复时，轮询
  // 不再重复 start()（整树闪回加载页一轮 + 双份全量 IPC）
  const readyRef = useRef(false)
  useEffect(() => { readyRef.current = ready }, [ready])
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
    // 136#5：会中期连续失败计数——一次瞬时坏读不再撕裂整个 UI
    let sessionFailures = 0
    // 111#4：错误页恢复轮询的 single-flight——错误页期间每个后端事件/每次
    // 窗口聚焦都会再触发 fail，原先每路各自开一个每秒轮询（N 路并行 IPC），
    // 就绪时还并发调 start() 双 bootstrap
    // 117 轮 C3：占用标志带代际——换代（session_started）后新错误页的
    // 轮询武装不再被旧代在途轮询的陈旧占用静默吞掉；旧轮询退出时只释放
    // 自己的占用
    let recoveryPollGeneration: number | null = null
    // 106#1/111#5：错误页统一挂恢复轮询——轮询启动面直到 ready 自动重跑
    // start()；intervalMs 区分「仍在初始化」（1s）与常规错误页（3s）。
    // 111#5：原先只覆盖 startup_not_ready，database_busy 耗尽/20s 超时/真实
    // 错误落错误页后，DataDirectoryPanel「重试初始化」成功也无人清错误态，
    // 面板说正常、整页仍显示失败——如今任一错误页都随 overview.ready 自愈
    const armRecoveryPoll = (expectedGeneration: number, intervalMs: number, observeError: boolean) => {
      // 同代已有在途轮询才拦（single-flight）；跨代占用说明是换代前遗留，
      // 直接接管——旧轮询醒来时代际不符会退出且不释放新占用
      if (recoveryPollGeneration === expectedGeneration || !alive() || generation.current !== expectedGeneration) return
      recoveryPollGeneration = expectedGeneration
      const release = () => { if (recoveryPollGeneration === expectedGeneration) recoveryPollGeneration = null }
      const poll = async () => {
        for (;;) {
          await new Promise(resolve => window.setTimeout(resolve, intervalMs))
          if (!alive() || generation.current !== expectedGeneration) break
          try {
            const overview = await storageBackend.overview()
            if (!alive() || generation.current !== expectedGeneration) break
            if (overview.ready) {
              release()
              // 117 轮 C1：非轮询路径已恢复就绪（聚焦触发 sync 成功/迟到
              // bootstrap 成功）——直接退出，不再重复整轮 bootstrap+全量快照
              if (readyRef.current) return
              start()
              return
            }
            // startup_not_ready 轮询沿 106#1 语义观察真实失败（overview.error
            // 出现即转常规错误页）；常规错误页只等 ready，不再反复改写文案
            if (observeError && overview.error) {
              release()
              fail({ code: overview.error.code, message: overview.error.message }, expectedGeneration)
              return
            }
          } catch {
            // 轮询本身失败（如正在换库）——下一轮继续，直到存活/代际变化退出
          }
        }
        release()
      }
      void poll()
    }
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
      // 唯一回正通道是不可发现的「切走再切回窗口」
      if (code === 'startup_not_ready') {
        settled = true
        setStartupError('本地后端仍在初始化（首次启动的大数据目录迁移或杀毒扫描可能较慢），正在继续等待……此页面会在就绪后自动恢复。')
        setReady(false)
        armRecoveryPoll(expectedGeneration, 1000, true)
        return
      }
      // 136#5：会中期（ready 已立）非归类读错误保留快照——原先兜底分支不
      // 区分启动期/会中期，一次 io_error/internal 即把整树切错误页（全部
      // 页面卸载、本地 UI 状态清场），恢复只能靠 3s 轮询/下一事件成功后
      // 几秒级闪断；改为只报横幅 toast 保留界面，连续 5 次失败才落错误页
      if (readyRef.current && sessionFailures < 5) {
        sessionFailures += 1
        reportBackendError(error)
        return
      }
      transientRetries = 0
      settled = true
      reportBackendError(error)
      setStartupError(backendErrorMessage(error))
      setReady(false)
      // 111#5：常规错误页也挂恢复轮询（慢档 3s，只等 ready）——面板侧
      // 「重试初始化」成功、外部目录恢复等情况就绪后自动回正
      armRecoveryPoll(expectedGeneration, 3000, false)
    }
    const armTimeout = () => {
      if (timer !== undefined) window.clearTimeout(timer)
      settled = false
      timer = window.setTimeout(() => {
        if (!mounted.current || settled || !starting) return
        settled = true
        setStartupError('等待本地后端响应已超过 20 秒。请重试，或检查应用数据目录权限及其他知舟进程是否占用数据库。重试不会清除学习数据。')
        setReady(false)
        // 111#5：超时错误页同样挂恢复轮询——bootstrap 若只是慢而非死，
        // 就绪后自动重跑，不再依赖不可发现的窗口切换。
        // 117 轮 C2：必须 observeError=true——bootstrap 超时通常是持续
        // startup_not_ready（106#1 人群），慢初始化最终失败（overview.error
        // 置位）时要能转常规错误页；原先 3s/不观察档会抢占 fail 的
        // 1s/观察档，把「慢→失败」永久困在「仍在初始化…会自动恢复」
        armRecoveryPoll(generation.current, 1000, true)
      }, 20000)
    }
    const start = () => {
      // 111#4：同代重入守卫——恢复轮询就绪与用户重试并发调 start() 时
      // 原先并发双 bootstrap（结果幂等但往返翻倍）；换代会话重启不受影响
      if (starting && activeStartup === generation.current) return
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
        // 136#5：会中期失败被成功同步纠正——连续计数复位
        sessionFailures = 0
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
    // 128#7：import_full_backup 后端返回自由 Value——TS 断言静默通过，
    // 键形漂移（改名/缺失）时渲染层读出 undefined 空白；对齐
    // guardLearningPlan 范式做运行时守卫，坏形状以可定位错误暴露
    const raw = next as unknown as Record<string, unknown>
    if (typeof raw.workspaceId !== 'number' || typeof raw.lastEventId !== 'number'
      || raw.tasks === undefined || raw.notes === undefined || raw.resources === undefined
      || raw.projects === undefined || raw.preferences === undefined || raw.versions === undefined) {
      console.error('guardAppSnapshot: 导入快照形状断言失败（核心集合/versions/workspaceId 缺失，后端 JSON 键变更？）', next)
      throw new Error('导入返回的数据结构与当前应用版本不兼容，请更新应用后再试。')
    }
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
