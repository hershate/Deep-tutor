// Deep-tutor 剪藏 - popup：按钮触发剪藏 + 显示最近结果（含「打开并生成」链接）。

const statusEl = document.getElementById('status')
const lastEl = document.getElementById('last')
const btnSel = document.getElementById('clip-selection')
const btnPage = document.getElementById('clip-page')

function showStatus(text, ok) {
  statusEl.textContent = text
  statusEl.className = ok ? 'ok' : 'err'
}

function showLast(result) {
  if (!result || !result.ok) return
  lastEl.hidden = false
  lastEl.innerHTML = ''
  const title = document.createElement('div')
  title.textContent = `已剪藏：${result.title || '(无标题)'}`
  const link = document.createElement('a')
  link.href = result.openUrl
  link.target = '_blank'
  link.rel = 'noreferrer'
  link.textContent = '打开并生成学习方案 →'
  lastEl.appendChild(title)
  lastEl.appendChild(link)
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
