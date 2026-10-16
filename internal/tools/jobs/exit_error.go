package jobs

import "errors"

// ExitError is a job function's failure when its process ran and ended on its
// own terms: Code is its exit status, Signal the signal that ended it (zero
// when none did). The producer knows which it was, so no reader has to read it
// back out of the error text, which stays the underlying error's own.
type ExitError struct {
	Code   int
	Signal int
	Err    error
}

func (e *ExitError) Error() string { return e.Err.Error() }
func (e *ExitError) Unwrap() error { return e.Err }

// exitOf returns the typed exit carried anywhere in err's chain.
func exitOf(err error) (*ExitError, bool) {
	var ex *ExitError
	return ex, errors.As(err, &ex)
}
