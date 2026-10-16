package notify

import (
	"strings"
	"testing"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/config"
	"reasonix/internal/platform/diskhealth"
)

type recorder struct{ sent []Message }

func (r *recorder) Send(m Message) error { r.sent = append(r.sent, m); return nil }

const gib = int64(1) << 30

func TestADiskNoticeNeedsItsOwnSwitchAsWellAsTheMasterOne(t *testing.T) {
	n := diskhealth.Notice{Dir: "/home/me/projects/app", Free: 2 * gib, Total: 500 * gib}
	for name, c := range map[string]struct {
		cfg  config.NotificationsConfig
		want int
	}{
		"both on":        {config.NotificationsConfig{Enabled: true, DiskLow: true}, 1},
		"master off":     {config.NotificationsConfig{Enabled: false, DiskLow: true}, 0},
		"own switch off": {config.NotificationsConfig{Enabled: true, DiskLow: false}, 0},
	} {
		r := &recorder{}
		SendDiskLow(r, i18n.CatalogFor("en"), c.cfg, n)
		if len(r.sent) != c.want {
			t.Errorf("%s: sent %d, want %d", name, len(r.sent), c.want)
		}
	}
}

func TestADiskNoticeNamesTheFreeSpaceAndOnlyTheFolderName(t *testing.T) {
	r := &recorder{}
	SendDiskLow(r, i18n.CatalogFor("en"), config.NotificationsConfig{Enabled: true, DiskLow: true},
		diskhealth.Notice{Dir: "/home/me/secret-client/app", Free: 2*gib + gib/2, Total: 500 * gib})
	if len(r.sent) != 1 {
		t.Fatalf("sent %d", len(r.sent))
	}
	body := r.sent[0].Body
	if !strings.Contains(body, "2.5 GB") || !strings.Contains(body, "app") || strings.Contains(body, "secret-client") {
		t.Fatalf("body = %q, want the free space and the folder's own name, not its path", body)
	}
}

func TestADiskNoticeReadsInTheChosenLanguage(t *testing.T) {
	r := &recorder{}
	SendDiskLow(r, i18n.CatalogFor("zh"), config.NotificationsConfig{Enabled: true, DiskLow: true},
		diskhealth.Notice{Dir: "/w/app", Free: 3 * gib, Total: 500 * gib})
	if !strings.Contains(r.sent[0].Body, "只剩 3.0 GB") || !strings.Contains(r.sent[0].Body, "app") {
		t.Fatalf("body = %q", r.sent[0].Body)
	}
}
