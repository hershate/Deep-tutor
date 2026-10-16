package i18n

// The text of every desktop notification, per language; each catalogue embeds its own.

var notifyEN = NotifyMessages{
	AskTimeoutMessages: AskTimeoutMessages{
		CmdAskTimeout: "skip a question nobody answers after a wait", AskTimeoutFmt: "ask timeout: %s — set it with /ask-timeout <seconds|off>", AskTimeoutOff: "off", AskTimeoutBadFmt: "ask-timeout: %q is neither a number of seconds nor off",
	},
	NotifyTitle:         "Reasonix",
	NotifyTurnDone:      "The turn finished",
	NotifyTurnFailed:    "The turn failed",
	NotifyApproval:      "Waiting for your approval",
	NotifyAsk:           "The model asked you something",
	NotifyFeedbackReply: "Your feedback has a new reply",
	NotifyDiskLow:       "Disk space is low: %s left on the drive holding %s",
}

var notifyZH = NotifyMessages{
	AskTimeoutMessages: AskTimeoutMessages{
		CmdAskTimeout: "设置提问无人回答多久后自动跳过", AskTimeoutFmt: "提问等待超时：%s — 用 /ask-timeout <秒数|off> 修改", AskTimeoutOff: "关闭", AskTimeoutBadFmt: "ask-timeout：%q 既不是秒数也不是 off",
	},
	NotifyTitle:         "Reasonix",
	NotifyTurnDone:      "这一轮结束了",
	NotifyTurnFailed:    "这一轮失败了",
	NotifyApproval:      "有操作在等你批准",
	NotifyAsk:           "模型问了你一个问题",
	NotifyFeedbackReply: "你的反馈有新回复",
	NotifyDiskLow:       "磁盘空间不足：存放 %[2]s 的磁盘只剩 %[1]s",
}

var notifyZHTW = NotifyMessages{
	AskTimeoutMessages: AskTimeoutMessages{
		CmdAskTimeout: "設定提問無人回答多久後自動跳過", AskTimeoutFmt: "提問等待逾時：%s — 用 /ask-timeout <秒數|off> 修改", AskTimeoutOff: "關閉", AskTimeoutBadFmt: "ask-timeout：%q 既不是秒數也不是 off",
	},
	NotifyTitle:         "Reasonix",
	NotifyTurnDone:      "這一輪結束了",
	NotifyTurnFailed:    "這一輪失敗了",
	NotifyApproval:      "有操作在等你核准",
	NotifyAsk:           "模型問了你一個問題",
	NotifyFeedbackReply: "你的回饋有新回覆",
	NotifyDiskLow:       "磁碟空間不足：存放 %[2]s 的磁碟只剩 %[1]s",
}
