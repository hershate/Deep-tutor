// Deep-tutor 剪藏 - 选项页：保存服务器地址 + Token；测试连接（GET /api/settings）。

const urlEl = document.getElementById('server-url')
const tokenEl = document.getElementById('token')
const statusEl = document.getElementById('status')

function showStatus(text, ok) {
  statusEl.textContent = text
  statusEl.className = ok ? 'ok' : 'err'
}

async function load() {
  const { serverUrl, token } = await dtGetConfig()
  urlEl.value = serverUrl
  tokenEl.value = token
}

async function save() {
  const serverUrl = urlEl.value.trim().replace(/\/+$/, '')
  if (serverUrl && !/^https?:\/\//i.test(serverUrl)) {
    showStatus('服务器地址须以 http(s):// 开头', false)
    return
  }
  await chrome.storage.local.set({
    serverUrl: serverUrl || DEFAULT_SERVER_URL,
    token: tokenEl.value.trim(),
  })
  showStatus('已保存', true)
}

async function test() {
  const serverUrl = (urlEl.value.trim() || DEFAULT_SERVER_URL).replace(/\/+$/, '')
  const token = tokenEl.value.trim()
  if (!token) {
    showStatus('请先填写 Token', false)
    return
  }
  showStatus('测试中…', true)
  try {
    const res = await fetch(`${serverUrl}/api/settings`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (res.ok) {
      const body = await res.json().catch(() => ({}))
      showStatus(`连接成功（模型：${body.anthropic_model || '未知'}）`, true)
    } else if (res.status === 401) {
      showStatus('连接成功但 Token 无效（401）', false)
    } else {
      showStatus(`服务器响应异常：HTTP ${res.status}`, false)
    }
  } catch {
    showStatus(`无法连接 ${serverUrl}（后端未启动？）`, false)
  }
}

document.getElementById('save-btn').addEventListener('click', save)
document.getElementById('test-btn').addEventListener('click', test)
load()
