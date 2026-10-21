package cli

import (
	"deep-tutor/internal/base/i18n"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/platform/notify"
)

var newNotificationSender = func() notify.Sender { return notify.NewPlatformSender() }

// withNotifications adds system notifications to CLI event streams when configured.
// The CLI reads its config once per process, so the holder never moves after it
// is made; a window that can flip the switch mid-run stores into its own.
func withNotifications(sink event.Sink, cfg *config.Config) event.Sink {
	if cfg == nil || !cfg.Notifications.Enabled && !cfg.Notifications.Sound {
		return sink
	}
	return notify.NewSink(sink, newNotificationSender(), i18n.M, notify.NewSettings(cfg.Notifications))
}
