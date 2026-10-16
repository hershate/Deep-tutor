//go:build linux

package notify

import (
	"os/exec"

	"reasonix/internal/base/secrets"
)

// PlatformSender delivers notifications through the host OS.
type PlatformSender struct{}

// NewPlatformSender returns the best-effort sender for the current platform.
func NewPlatformSender() PlatformSender { return PlatformSender{} }

func (PlatformSender) Send(m Message) error {
	cmd := exec.Command("notify-send", m.Title, m.Body)
	cmd.Env = secrets.ProcessEnv()
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }()
	return nil
}

// Chime plays the freedesktop "complete" sound through whichever player the
// desktop has, and does nothing where there is none.
func (PlatformSender) Chime() error {
	for _, argv := range [][]string{
		{"canberra-gtk-play", "-i", "complete"},
		{"paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"},
	} {
		path, err := exec.LookPath(argv[0])
		if err != nil {
			continue
		}
		cmd := exec.Command(path, argv[1:]...)
		cmd.Env = secrets.ProcessEnv()
		if err := cmd.Start(); err != nil {
			return err
		}
		go func() { _ = cmd.Wait() }()
		return nil
	}
	return nil
}
