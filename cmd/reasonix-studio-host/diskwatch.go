package main

import (
	"context"
	"time"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/config"
	"reasonix/internal/platform/diskhealth"
	"reasonix/internal/platform/notify"
)

// diskWatchEvery is how often the volumes are looked at: a build fills a disk
// over minutes, and a statfs per folder is cheap.
const diskWatchEvery = 5 * time.Minute

// watchDisk looks at the volumes under dirs on a timer and says when one runs
// short of space. The switches are read each time, so turning the notice off or
// on in the window takes effect without a restart, and the language is the one
// the window is set to now.
func watchDisk(ctx context.Context, set *notify.Settings, sender notify.Sender, dirs []string, every time.Duration, read func(string) (diskhealth.Volume, bool)) {
	monitor := diskhealth.NewMonitor()
	tick := time.NewTicker(every)
	defer tick.Stop()
	for {
		cfg := set.Load()
		if cfg.Enabled && cfg.DiskLow {
			for _, n := range monitor.Check(time.Now(), dirs, read) {
				say := i18n.CatalogFor(config.LoadForEdit(config.UserConfigPath()).DesktopLanguage())
				notify.SendDiskLow(sender, say, cfg, n)
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
	}
}

// startDiskWatch begins the watch and returns the sender it speaks through, so
// the hub's notices and the disk warning leave by the same door.
func startDiskWatch(ctx context.Context, set *notify.Settings, dirs []string) notify.Sender {
	sender := notify.NewPlatformSender()
	go watchDisk(ctx, set, sender, dirs, diskWatchEvery, diskhealth.Read)
	return sender
}
