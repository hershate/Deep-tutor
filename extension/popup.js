// Deep-tutor 剪藏 - popup：按钮触发剪藏 + 显示最近结果（含「打开并生成」链接）。

const statusEl = document.getElementById('status')
const lastEl = document.getElementById('last')
const btnSel = document.getElementById('clip-selection')
const btnPage = document.getElementById('clip-page')

function showStatus(text, ok) {
  statusEl.textContent = text
  statusEl.className = ok ? 'ok' : 'err'
}

function fmtAgo(at) {
  if (!at) return ''
  const s = Math.max(0, (Date.now() - at) / 1000)
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return new Date(at).toLocaleDateString()
}

function showLast(result) {
  if (!result) return
  lastEl.hidden = false
  lastEl.innerHTML = ''
  const head = document.createElement('div')
  head.className = 'time'
  head.textContent = result.at ? `最近剪藏 · ${fmtAgo(result.at)}` : '最近剪藏'
  lastEl.appendChild(head)
  if (result.ok) {
    const title = document.createElement('div')
    title.textContent = `已剪藏：${result.title || '(无标题)'}`
    const link = document.createElement('a')
    link.href = result.openUrl
    link.target = '_blank'
    link.rel = 'noreferrer'
    link.textContent = '打开并生成学习方案 →'
    lastEl.appendChild(title)
    lastEl.appendChild(link)
  } else {
    // 失败结果同样展示（右键剪藏失败时 badge 仅 3 秒，原因需在此可见）
    const err = document.createElement('div')
    err.className = 'err'
    err.textContent = `失败：${result.error || '未知错误'}`
    lastEl.appendChild(err)
  }
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  return tab
}

async function getSelection(tabId) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => window.getSelection()?.toString() ?? '',
  })
  return (result || '').trim()
}

async function doClip(kind) {
  btnSel.disabled = btnPage.disabled = true
  showStatus('剪藏中…', true)
  try {
    const tab = await activeTab()
    let result
    if (kind === 'text') {
      const text = await getSelection(tab.id)
      if (!text) {
        showStatus('请先在页面中选中要剪藏的文本', false)
        return
      }
      result = await dtClip('text', text, tab.title || '')
    } else {
      if (!/^https?:\/\//i.test(tab.url || '')) {
        showStatus('当前页面不可剪藏（仅 http(s) 页面）', false)
        return
      }
      result = await dtClip('url', tab.url, tab.title || '')
    }
    if (result.ok) {
      showStatus('剪藏成功', true)
      showLast(result)
    } else {
      showStatus(result.error, false)
    }
  } catch (e) {
    showStatus(`剪藏失败：${e.message || e}`, false)
  } finally {
    btnSel.disabled = btnPage.disabled = false
  }
}

btnSel.addEventListener('click', () => doClip('text'))
btnPage.addEventListener('click', () => doClip('url'))
document.getElementById('options-link').addEventListener('click', (e) => {
  e.preventDefault()
  chrome.runtime.openOptionsPage()
})

// 展示最近一次剪藏结果
chrome.storage.local.get(['lastResult']).then(({ lastResult }) => showLast(lastResult))
