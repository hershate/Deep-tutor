// Deep-tutor 剪藏 - 配置读写 + 剪藏 API 调用（background 与 popup 共用）。
// 配置存 chrome.storage.local：serverUrl（默认 http://localhost:41173）、token（JWT 或 AUTH_TOKEN）。

const DEFAULT_SERVER_URL = 'http://localhost:41173'

async function dtGetConfig() {
  const cfg = await chrome.storage.local.get(['serverUrl', 'token'])
  return {
    serverUrl: (cfg.serverUrl || DEFAULT_SERVER_URL).replace(/\/+$/, ''),
    token: cfg.token || '',
  }
}

// 剪藏：POST /api/materials/clip {kind, content, title}
// 成功 -> { ok: true, sessionId, openUrl }；失败 -> { ok: false, error }
// 成功与失败都持久化到 lastResult（popup 可展示最近一次的失败原因，
// 否则右键剪藏失败只有 3 秒红 badge，用户无从得知原因）
async function dtClip(kind, content, title) {
  const { serverUrl, token } = await dtGetConfig()
  async function fail(error) {
    const result = { ok: false, error, at: Date.now() }
    await chrome.storage.local.set({ lastResult: result })
    return result
  }
  if (!token) return fail('未配置 Token（请在扩展选项页填写）')
  let res
  try {
    res = await fetch(`${serverUrl}/api/materials/clip`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ kind, content, title: title || null }),
    })
  } catch {
    return fail(`无法连接 ${serverUrl}（后端未启动？）`)
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const msg = body?.error?.message || `HTTP ${res.status}`
    return fail(res.status === 401 ? 'Token 无效或已过期（请在选项页更新）' : msg)
  }
  const result = {
    ok: true,
    sessionId: body.session_id,
    title: body.title || '',
    openUrl: `${serverUrl}${body.open_path}`,
    at: Date.now(),
  }
  await chrome.storage.local.set({ lastResult: result })
  return result
}

//  badge 反馈：✓ 绿 / ✗ 红，3 秒后清除
async function dtBadge(ok) {
  await chrome.action.setBadgeText({ text: ok ? '✓' : '✗' })
  await chrome.action.setBadgeBackgroundColor({ color: ok ? '#16a34a' : '#dc2626' })
  setTimeout(() => chrome.action.setBadgeText({ text: '' }), 3000)
}
