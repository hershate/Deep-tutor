package agent

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	"deep-tutor/internal/base/testenv"

	"go.uber.org/goleak"
)

func TestMain(m *testing.M) {
	// Stream body retries use multi-second backoff in production; collapse it
	// in package tests so recovery suites stay deterministic and fast.
	streamRetrySleep = func(ctx context.Context, _ time.Duration) bool {
		return ctx.Err() == nil
	}
	// A machine that names its own home outranks a test's stated one, because
	// the current spelling wins the lookup. The guard clears both spellings
	// before any test runs, so what a test states is then what the code reads.
	cleanupUserState, err := testenv.IsolateUserState()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	goleak.VerifyTestMain(m, append(liveRunLeakAllowances(),
		goleak.Cleanup(func(exitCode int) {
			cleanupUserState()
			os.Exit(exitCode)
		}))...)
}

// A live measurement opens a real HTTP/2 connection whose read loop the
// provider owns and no test can reach to close. Only that goroutine is
// forgiven, and only under the env gate that asks for the traffic: forgiving
// everything parked on IO would make the live harness the one place its own
// leaks cannot be seen.
func liveRunLeakAllowances() []goleak.Option {
	if os.Getenv("REASONIX_LIVE_REFUSAL") != "1" {
		return nil
	}
	return []goleak.Option{goleak.IgnoreAnyFunction("net/http.(*http2ClientConn).readLoop")}
}
