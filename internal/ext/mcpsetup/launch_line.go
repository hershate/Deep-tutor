package mcpsetup

import (
	"strings"

	"reasonix/internal/base/i18n"
	"reasonix/internal/base/secrets"
	"reasonix/internal/base/textutil"
	"reasonix/internal/contract/config"
)

// LaunchLine is what starting e runs or contacts, for a person deciding on it:
// redacted, without env values, and stripped of anything that could repaint a
// terminal or reorder the line. Deleting is what lets redaction match a key a
// zero-width character split, so a line that lost characters says so, and says
// it first: callers cut a line to fit from its end, never from its start.
func LaunchLine(e config.PluginEntry) string {
	line, removed := launchLine(e)
	if removed {
		return i18n.M.LaunchHiddenRemoved + " " + line
	}
	return line
}

func launchLine(e config.PluginEntry) (string, bool) {
	removed := false
	clean := func(s string) string {
		out, gone := textutil.ScrubLaunch(s)
		removed = removed || gone
		return out
	}
	line := RedactURL(clean(e.URL))
	if strings.TrimSpace(e.Command) != "" {
		args := make([]string, len(e.Args))
		for i, a := range e.Args {
			args[i] = clean(a)
		}
		parts := append([]string{secrets.RedactConfigValue("", clean(e.Command))}, secrets.RedactArgs(args)...)
		line = strings.Join(parts, " ")
	}
	return textutil.SanitizeLaunch(line), removed
}
