// serve_config.go — the [serve] section: what the HTTP frontend is allowed to
// do and how many panes it may hold open.
//
// It lives apart from the rest of the configuration because it is the section
// with its own rules: it is the user's alone (a project file cannot set it),
// it is read by the serve layer rather than by the kernel, and two of its
// keys are ceilings rather than preferences.
package config

// ServeConfig controls the HTTP serve frontend security settings.
type ServeConfig struct {
	// AuthMode selects the authentication mode: "none" (default), "token" (a
	// pre-shared token in the URL query string) or "password" (a login page
	// with bcrypt password verification).
	AuthMode string `toml:"auth_mode"`
	// Token is a pre-shared token for auth_mode = "token". When empty, a
	// cryptographically random token is generated at startup and printed.
	Token string `toml:"token"`
	// PasswordHash is a bcrypt hash of the password for auth_mode = "password".
	// Generate one with: reasonix serve --hash-password --password '...'
	PasswordHash string `toml:"password_hash"`
	// BehindProxy says the server sits behind a trusted reverse proxy that sets
	// X-Forwarded-For and X-Forwarded-Proto; those are used for rate limiting
	// and cookie decisions, and forged headers are ignored when false (default).
	BehindProxy bool `toml:"behind_proxy"`
	// SharePort fixes the port of the phone-access LAN link; zero picks a free
	// one each time the door opens. User-global like the rest of [serve].
	SharePort int `toml:"share_port"`
	// RememberPairedDevices keeps paired phones across restarts: the next process
	// reopens the share where it was and adopts the devices that paired against it.
	// Off by default, like every other promise about a device you are not holding.
	RememberPairedDevices bool `toml:"remember_paired_devices"`
	// MaxPanes caps how many panes one hub holds open at once: a pane is a
	// whole runtime, so an unbounded number of them grows the process until it
	// cannot answer. Zero means DefaultMaxPanes.
	MaxPanes int `toml:"max_panes"`
}

// DefaultMaxPanes is how many panes a hub holds open when the user has not said
// otherwise: enough for one person working across several projects, few enough
// that the process stays answerable.
const DefaultMaxPanes = 16

// PaneLimit is the ceiling this configuration puts on open panes. A
// hand-written zero or less falls back to the default rather than removing the
// ceiling — an unbounded hub is the thing the cap exists to prevent.
func (s ServeConfig) PaneLimit() int {
	if s.MaxPanes > 0 {
		return s.MaxPanes
	}
	return DefaultMaxPanes
}
