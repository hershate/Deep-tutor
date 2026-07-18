// Deep-tutor 剪藏 - 后台 service worker：右键菜单（选中文本/本页链接）-> 剪藏。
importScripts('common.js')

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: 'dt-clip-selection',
    title: '剪藏选中内容到 Deep-tutor',
    contexts: ['selection'],
  })
  chrome.contextMenus.create({
    id: 'dt-clip-page',
    title: '剪藏本页链接到 Deep-tutor',
    contexts: ['page'],
  })
})

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  let result
  if (info.menuItemId === 'dt-clip-selection') {
    const text = (info.selectionText || '').trim()
    if (!text) return
    result = await dtClip('text', text, tab?.title || '')
  } else if (info.menuItemId === 'dt-clip-page') {
    const url = info.pageUrl || tab?.url || ''
    if (!/^https?:\/\//i.test(url)) return
    result = await dtClip('url', url, tab?.title || '')
  } else {
    return
  }
  await dtBadge(result.ok)
})
