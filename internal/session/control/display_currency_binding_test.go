package control

import (
	"errors"
	"os"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
)

func TestDisplayCurrencyBindingIsQuietCanonicalAndToleratesReadFailure(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	write := func(body string) {
		t.Helper()
		if err := os.WriteFile(config.UserConfigPath(), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("[billing]\ndisplay_currency = \"USD\"\n")
	var group DisplayCurrencyGroup
	oldContext, newContext := &event.QuoteContext{}, &event.QuoteContext{DisplayCurrency: "CNY"}
	notices := 0
	sink := event.FuncSink(func(event.Event) { notices++ })
	old := New(Options{Sink: sink, QuoteContext: oldContext})
	next := New(Options{Sink: sink, QuoteContext: newContext})
	t.Cleanup(old.Close)
	t.Cleanup(next.Close)
	display := ""
	project := func(currency string) { display = currency }
	if err := group.Bind(nil, old, project); err != nil {
		t.Fatal(err)
	}
	const malformed = "[billing]\ndisplay_currency = ["
	write(malformed)
	if err := group.Bind(old, next, project); err != nil {
		t.Fatal(err)
	}
	if _, live := group.members[next]; !live || len(group.members) != 1 || next.currencyGroup() != &group {
		t.Fatal("read failure prevented replacement enrollment")
	}
	if newContext.DisplayCurrency != "CNY" || display != "USD" || notices != 0 {
		t.Fatal("read failure changed the built projections or emitted a notice")
	}
	if raw, err := os.ReadFile(config.UserConfigPath()); err != nil || string(raw) != malformed {
		t.Fatalf("malformed config changed: %q, %v", raw, err)
	}
	write("[billing]\ndisplay_currency = \"CNY\"\n")
	if err := group.Bind(next, next, project); err != nil {
		t.Fatal(err)
	}
	if newContext.DisplayCurrency != "CNY" || display != "CNY" || notices != 0 {
		t.Fatalf("canonical quiet projection = %q %q, notices %d", newContext.DisplayCurrency, display, notices)
	}
	if err := group.Bind(next, next, project); err != nil {
		t.Fatal(err)
	}
	if notices != 0 || len(group.members) != 1 {
		t.Fatal("same-generation bind duplicated notice or membership")
	}
	old.Close()
	if len(group.members) != 1 {
		t.Fatal("old teardown removed replacement")
	}
	write(malformed)
	if err := group.Bind(old, next, project); !errors.Is(err, ErrDisplayCurrencyRetired) {
		t.Fatalf("retired previous bind = %v", err)
	}
	next.Close()
	if err := group.Bind(nil, next, project); !errors.Is(err, ErrDisplayCurrencyRetired) {
		t.Fatalf("closed controller bind = %v", err)
	}
	if len(group.members) != 0 {
		t.Fatal("closed member remained registered")
	}
}

func TestDisplayCurrencyBindingOrdersProjectionBeforeFanout(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	if err := os.WriteFile(config.UserConfigPath(), []byte("[billing]\ndisplay_currency = \"CNY\"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	var group DisplayCurrencyGroup
	quote := &event.QuoteContext{}
	display := ""
	sink := event.NewCostQuoteSink(event.FuncSink(func(e event.Event) {
		if e.Code == event.NoticeCodeDisplayCurrency {
			display = e.Detail
		}
	}), quote)
	next := New(Options{Sink: sink, QuoteContext: quote})
	t.Cleanup(next.Close)
	entered, release, done := make(chan struct{}), make(chan struct{}), make(chan error, 1)
	go func() {
		done <- group.Bind(nil, next, func(currency string) { close(entered); <-release; display = currency })
	}()
	<-entered
	if group.mu.TryLock() {
		group.mu.Unlock()
		t.Error("projection callback runs outside group serialization")
	}
	emitted := make(chan struct{})
	go func() {
		group.mu.Lock()
		group.emitLocked(next, event.Event{Kind: event.Notice, Code: event.NoticeCodeDisplayCurrency, Detail: "USD"})
		group.mu.Unlock()
		close(emitted)
	}()
	close(release)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	<-emitted
	if quote.DisplayCurrency != "USD" || display != "USD" {
		t.Fatalf("projection overtook notice: quote %q, frontend %q", quote.DisplayCurrency, display)
	}
}
