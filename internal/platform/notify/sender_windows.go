//go:build windows

package notify

import (
	"syscall"

	"git.sr.ht/~jackmordaunt/go-toast/v2"

	"reasonix/internal/platform/appidentity"
)

// PlatformSender delivers notifications through the Windows Toast API.
type PlatformSender struct{}

// NewPlatformSender returns the best-effort sender for the current platform.
func NewPlatformSender() PlatformSender {
	_ = toast.SetAppData(toast.AppData{AppID: appidentity.AppUserModelID})
	toast.SetActivationCallback(func(string, []toast.UserData) { activated() })
	return PlatformSender{}
}

func (PlatformSender) Send(m Message) error {
	notification := toast.Notification{
		AppID: appidentity.AppUserModelID,
		Title: m.Title,
		Body:  m.Body,
	}
	return notification.Push()
}

var (
	user32                = syscall.NewLazyDLL("user32.dll")
	messageBeep           = user32.NewProc("MessageBeep")
	allowSetForegroundWin = user32.NewProc("AllowSetForegroundWindow")
)

func init() {
	const asfwAny = ^uintptr(0)
	allowForeground = func() { _, _, _ = allowSetForegroundWin.Call(asfwAny) }
}

// Chime plays the system's asterisk sound, which is the one the person already
// hears for a finished task elsewhere; it does not depend on toast settings.
func (PlatformSender) Chime() error {
	const iconAsterisk = 0x40
	if r, _, err := messageBeep.Call(iconAsterisk); r == 0 {
		return err
	}
	return nil
}
