package i18n

// NotifyMessages is the text of every desktop notification; Messages embeds it,
// so a new notification kind adds a line here and not to the catalogue struct.
type NotifyMessages struct {
	NotifyTitle         string // desktop notification title
	NotifyTurnDone      string // the turn finished
	NotifyTurnFailed    string // the turn failed
	NotifyApproval      string // something is waiting to be approved
	NotifyAsk           string // the model asked a question
	NotifyFeedbackReply string // a feedback report has a new reply
	NotifyDiskLow       string // a volume is nearly full; formatted with the free space, then the folder's name
	AskTimeoutMessages
}

// AskTimeoutMessages is the /ask-timeout command's text.
type AskTimeoutMessages struct {
	CmdAskTimeout    string // the command's hint
	AskTimeoutFmt    string // status line, one %s state
	AskTimeoutOff    string // the word for the timeout being off
	AskTimeoutBadFmt string // argument that is neither a number nor off, one %s
}
