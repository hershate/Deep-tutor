package notify

import (
	"deep-tutor/internal/base/i18n"
	"deep-tutor/internal/contract/config"
)

// SendFeedbackReply announces a feedback reply under the same two switches as
// every other kind. The text carries no part of the reply.
func SendFeedbackReply(sender Sender, say i18n.Messages, cfg config.NotificationsConfig) {
	if !cfg.FeedbackReply {
		return
	}
	announce(sender, cfg, Message{Title: say.NotifyTitle, Body: say.NotifyFeedbackReply})
}
