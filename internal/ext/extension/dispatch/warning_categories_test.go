package dispatch

import (
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"

	"reasonix/internal/ext/extension"
	"reasonix/internal/ext/extension/protocol"
)

func TestWarningCategoriesDoNotSuppressEachOther(t *testing.T) {
	for _, downFirst := range []bool{false, true} {
		for _, typed := range []bool{false, true} {
			t.Run(fmt.Sprintf("downFirst=%t/typed=%t", downFirst, typed), func(t *testing.T) {
				point := extension.PointInputReceive
				chain := map[extension.InterceptorPoint][]extension.Contribution{
					point: {interceptor("first", point, 0), interceptor("second", point, 1)},
				}
				fake := &fakeClient{interceptFn: func(protocol.InterceptEvent, json.RawMessage) (protocol.InterceptResult, error) {
					return protocol.InterceptResult{}, timeoutError("test", point)
				}}
				var missing atomic.Bool
				warns, down := &warnRecorder{}, &warnRecorder{}
				opts := Options{Warn: warns.warn}
				if typed {
					opts.SidecarDown = func(id string, gotPoint extension.InterceptorPoint) {
						if gotPoint != point {
							t.Errorf("point = %s, want %s", gotPoint, point)
						}
						down.warn(id)
					}
				}
				d := New(chain, nil, func(string) Client {
					if missing.Load() {
						return nil
					}
					return fake
				}, nil, opts)
				for _, isMissing := range []bool{downFirst, !downFirst, downFirst, !downFirst} {
					missing.Store(isMissing)
					var calls sync.WaitGroup
					for range 16 {
						calls.Go(func() {
							payload := &InputPayload{Text: "unchanged"}
							result, err := d.Intercept(t.Context(), point, payload)
							if err != nil || result == nil || result.Blocked || payload.Text != "unchanged" {
								t.Errorf("optional dispatch = %+v, %v, payload %+v", result, err, payload)
							}
						})
					}
					calls.Wait()
				}
				wantWarn, wantDown := 4, 0
				if typed {
					wantWarn, wantDown = 2, 2
				}
				if warns.count() != wantWarn || down.count() != wantDown {
					t.Fatalf("Warn = %d, SidecarDown = %d; want %d, %d", warns.count(), down.count(), wantWarn, wantDown)
				}
				if typed && (!down.contains("first") || !down.contains("second")) {
					t.Fatal("typed notices must identify both extensions")
				}
				for _, id := range []string{"first", "second"} {
					prefix := fmt.Sprintf("extension %s failed at %s; skipping this optional extension: ", id, point)
					if !warns.contains(prefix + timeoutError("test", point).Error()) {
						t.Errorf("missing timeout warning for %s", id)
					}
					if !typed && !warns.contains(prefix+ErrNoLiveSidecar.Error()) {
						t.Errorf("missing sidecar fallback warning for %s", id)
					}
				}
				if fake.interceptCount() != 64 {
					t.Fatalf("live calls = %d, want 64", fake.interceptCount())
				}
			})
		}
	}
}

func TestWarningCategoriesKeepRequiredFailures(t *testing.T) {
	point := extension.PointInputReceive
	chain := map[extension.InterceptorPoint][]extension.Contribution{point: {interceptor("required", point, 0)}}
	for _, slotOwner := range []bool{false, true} {
		t.Run(fmt.Sprintf("slotOwner=%t", slotOwner), func(t *testing.T) {
			required := map[string]bool{"required": !slotOwner}
			var replacements map[extension.Slot]extension.ContributionSource
			if slotOwner {
				replacements = map[extension.Slot]extension.ContributionSource{extension.SlotSystemPrompt: owner("required")}
			}
			warns, down := &warnRecorder{}, &warnRecorder{}
			var client Client
			d := New(chain, replacements, func(string) Client { return client }, required, Options{
				Warn: warns.warn, SidecarDown: func(id string, _ extension.InterceptorPoint) { down.warn(id) },
			})
			timeout := timeoutError("required", point)
			for _, cause := range []error{timeout, ErrNoLiveSidecar, timeout, ErrNoLiveSidecar} {
				client = nil
				if errors.Is(cause, timeout) {
					client = &fakeClient{interceptFn: func(protocol.InterceptEvent, json.RawMessage) (protocol.InterceptResult, error) {
						return protocol.InterceptResult{}, timeout
					}}
				}
				_, err := d.Intercept(t.Context(), point, &InputPayload{Text: "hi"})
				var failure *FailureError
				if !errors.As(err, &failure) || !errors.Is(err, cause) || failure.Plugin != "required" || failure.Point != point {
					t.Fatalf("failure = %v, want required error wrapping %v", err, cause)
				}
			}
			if warns.count() != 0 || down.count() != 0 {
				t.Fatal("required failures must not emit optional-extension warnings")
			}
		})
	}
}
