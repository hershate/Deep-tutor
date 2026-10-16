package notify

import (
	"fmt"
	"path/filepath"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/config"
	"reasonix/internal/platform/diskhealth"
)

// SendDiskLow announces a nearly full volume under the same two switches as
// every other kind, plus its own. The text names the folder only by its last
// element: a notification is read over a shoulder and sits in a log.
func SendDiskLow(sender Sender, say i18n.Messages, cfg config.NotificationsConfig, n diskhealth.Notice) {
	if !cfg.DiskLow {
		return
	}
	announce(sender, cfg, Message{Title: say.NotifyTitle, Body: fmt.Sprintf(say.NotifyDiskLow, freeText(n.Free), filepath.Base(n.Dir))})
}

// freeText is a size a person reads at a glance, in binary units.
func freeText(b int64) string {
	const gib, mib = int64(1) << 30, int64(1) << 20
	if b >= gib {
		return fmt.Sprintf("%.1f GB", float64(b)/float64(gib))
	}
	return fmt.Sprintf("%d MB", b/mib)
}
