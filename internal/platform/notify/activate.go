package notify

import "sync/atomic"

var onActivate atomic.Pointer[func()]

// allowForeground is the platform's hook for passing on the right to take the
// foreground, which a click grants to this process and not to the shell.
var allowForeground = func() {}

// OnActivate runs fn when the user clicks a notification this process sent.
// Only platforms whose notifications call back into the sender ever fire it.
func OnActivate(fn func()) { onActivate.Store(&fn) }

// activated is the platform sender's way of reporting a click.
func activated() {
	allowForeground()
	if fn := onActivate.Load(); fn != nil {
		(*fn)()
	}
}

var onSendFailure atomic.Pointer[func(error)]

// OnSendFailure runs fn with the error of a notification that could not be
// delivered; nil stops reporting.
func OnSendFailure(fn func(error)) {
	if fn == nil {
		onSendFailure.Store(nil)
		return
	}
	onSendFailure.Store(&fn)
}

func sendFailed(err error) {
	if fn := onSendFailure.Load(); fn != nil {
		(*fn)(err)
	}
}
