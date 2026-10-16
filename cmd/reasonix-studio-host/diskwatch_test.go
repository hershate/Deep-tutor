package main

import (
	"context"
	"sync"
	"testing"
	"time"

	"reasonix/internal/contract/config"
	"reasonix/internal/platform/diskhealth"
	"reasonix/internal/platform/notify"
)

type sentMessages struct {
	mu   sync.Mutex
	sent []notify.Message
}

func (s *sentMessages) Send(m notify.Message) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.sent = append(s.sent, m)
	return nil
}

func (s *sentMessages) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.sent)
}

const gigabyte = int64(1) << 30

func lowVolume(string) (diskhealth.Volume, bool) {
	return diskhealth.Volume{Free: 2 * gigabyte, Total: 500 * gigabyte}, true
}

func runWatch(t *testing.T, cfg config.NotificationsConfig) int {
	t.Helper()
	sender := &sentMessages{}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		watchDisk(ctx, notify.NewSettings(cfg), sender, []string{"/w", "/w/.state"}, time.Millisecond, lowVolume)
		close(done)
	}()
	time.Sleep(50 * time.Millisecond)
	cancel()
	<-done
	return sender.count()
}

// A volume that stays low is said once however many times it is looked at, and
// two folders on it do not make two notices.
func TestTheDiskWatchSaysALowVolumeOnce(t *testing.T) {
	if got := runWatch(t, config.NotificationsConfig{Enabled: true, DiskLow: true}); got != 1 {
		t.Fatalf("sent %d notices, want exactly one", got)
	}
}

func TestTheDiskWatchHonoursTheSwitches(t *testing.T) {
	if got := runWatch(t, config.NotificationsConfig{Enabled: true, DiskLow: false}); got != 0 {
		t.Fatalf("own switch off sent %d, want none", got)
	}
	if got := runWatch(t, config.NotificationsConfig{Enabled: false, DiskLow: true}); got != 0 {
		t.Fatalf("master switch off sent %d, want none", got)
	}
}
