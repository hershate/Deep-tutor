package config

import (
	"errors"
	"fmt"
	"strings"
	"time"
)

const (
	MinAskTimeoutSeconds     = 10
	MaxAskTimeoutSeconds     = 24 * 60 * 60
	DefaultAskTimeoutSeconds = 300
)

// ErrAskTimeoutOutOfRange marks a timeout outside what an ask can wait.
var ErrAskTimeoutOutOfRange = errors.New("ask timeout out of range")

// AskTimeoutSetting is the stored value. Whatever a hand edit puts there decodes
// without failing the whole file: a value that is not a plain integer, or one
// that does not fit, is kept as Invalid so the setting can refuse it by name
// instead of sending the user to the generic config repair.
type AskTimeoutSetting struct {
	Seconds int
	Invalid string
}

// UnmarshalTOML implements toml.Unmarshaler.
func (a *AskTimeoutSetting) UnmarshalTOML(v any) error {
	n, ok := v.(int64)
	if !ok || n > int64(MaxAskTimeoutSeconds)*1000 || n < -int64(MaxAskTimeoutSeconds)*1000 {
		a.Seconds, a.Invalid = 0, fmt.Sprintf("%v", v)
		return nil
	}
	a.Seconds, a.Invalid = int(n), ""
	return nil
}

// ValidateAskTimeoutSetting is ValidateAskTimeoutSeconds for a decoded value.
func ValidateAskTimeoutSetting(a AskTimeoutSetting) error {
	if a.Invalid != "" {
		return fmt.Errorf("%w: %q is not a whole number of seconds (0 or %d to %d)", ErrAskTimeoutOutOfRange, a.Invalid, MinAskTimeoutSeconds, MaxAskTimeoutSeconds)
	}
	return ValidateAskTimeoutSeconds(a.Seconds)
}

// ValidateAskTimeoutSeconds is the one rule for the timeout, applied to what is
// saved and to what a hand-edited file holds: 0 (off) or 10 to 86400.
func ValidateAskTimeoutSeconds(seconds int) error {
	if seconds != 0 && (seconds < MinAskTimeoutSeconds || seconds > MaxAskTimeoutSeconds) {
		return fmt.Errorf("%w: seconds must be 0 (off) or between %d and %d, not %d", ErrAskTimeoutOutOfRange, MinAskTimeoutSeconds, MaxAskTimeoutSeconds, seconds)
	}
	return nil
}

// AskTimeoutChecked is how long an `ask` question waits before the host skips
// it. Zero, the default, waits as long as it takes. A value the file holds that
// breaks the rule is an error, never a silent off or a wrapped duration.
func (c *Config) AskTimeoutChecked() (time.Duration, error) {
	if c == nil {
		return 0, nil
	}
	if err := ValidateAskTimeoutSetting(c.Agent.AskTimeoutSeconds); err != nil {
		return 0, err
	}
	return time.Duration(c.Agent.AskTimeoutSeconds.Seconds) * time.Second, nil
}

// SetAskTimeoutSeconds validates and stores the timeout; 0 turns it off.
func (c *Config) SetAskTimeoutSeconds(seconds int) error {
	if err := ValidateAskTimeoutSeconds(seconds); err != nil {
		return err
	}
	c.Agent.AskTimeoutSeconds = AskTimeoutSetting{Seconds: seconds}
	return nil
}

func renderAskTimeout(b *strings.Builder, c *Config) {
	if c.Agent.AskTimeoutSeconds.Seconds > 0 {
		fmt.Fprintf(b, "ask_timeout_seconds = %d   # skip an ask question nobody answered in this long; the model is told it timed out (0 = wait)\n", c.Agent.AskTimeoutSeconds.Seconds)
		return
	}
	b.WriteString("# ask_timeout_seconds = 300   # skip an ask question nobody answered in this long; the model is told it timed out (default 0: wait)\n")
}
