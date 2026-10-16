package control

import (
	"errors"
	"os"
	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reflect"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

func TestDisplayCurrencyGroupRebindsFullSinksAndReplacement(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	var group DisplayCurrencyGroup
	type probe struct {
		controller *Controller
		events     []event.Event
	}
	newProbe := func() *probe {
		p := &probe{}
		quote := &event.QuoteContext{
			DisplayCurrency: "CNY", CatalogProviderForModel: func(string) string { return "deepseek" },
		}
		sink := event.NewCostQuoteSink(event.FuncSink(func(e event.Event) { p.events = append(p.events, e) }), quote)
		p.controller = New(Options{Sink: sink, QuoteContext: quote})
		t.Cleanup(p.controller.Close)
		return p
	}
	locals := []*probe{newProbe(), newProbe(), newProbe()}
	remote := newProbe()
	for _, p := range locals {
		if err := group.Bind(nil, p.controller, nil); err != nil {
			t.Fatal(err)
		}
	}
	notice := event.Event{Kind: event.Notice, Level: event.LevelInfo, Code: event.NoticeCodeDisplayCurrency, Text: "saved currency", Detail: "USD"}
	emit := func(e event.Event) {
		group.mu.Lock()
		defer group.mu.Unlock()
		group.emitLocked(locals[0].controller, e)
	}
	emit(notice)
	usage := event.Event{Kind: event.Usage, ModelRef: "deepseek/deepseek-flash", Usage: &provider.Usage{PromptTokens: 1000, CompletionTokens: 1000}, Pricing: &provider.Pricing{Currency: "CNY", CacheHit: 0.02, Input: 1, Output: 4}}
	check := func(p *probe, want string) {
		t.Helper()
		p.controller.EmitExtensionEvent(usage)
		got := p.events[len(p.events)-1].CostQuote
		if got == nil || got.Selected == nil || got.Selected.Currency != want {
			t.Fatalf("future usage = %+v, want %s", got, want)
		}
	}
	for _, p := range locals {
		if len(p.events) != 1 || !reflect.DeepEqual(p.events[0], notice) {
			t.Fatalf("notice = %+v, want original notice exactly once", p.events)
		}
		check(p, "USD")
	}
	if len(remote.events) != 0 {
		t.Fatalf("remote received %d events", len(remote.events))
	}
	check(remote, "CNY")
	if err := os.WriteFile(config.UserConfigPath(), []byte("[billing]\ndisplay_currency = ["), 0600); err != nil {
		t.Fatal(err)
	}
	before := []int{len(locals[0].events), len(locals[1].events), len(locals[2].events)}
	if err := locals[0].controller.SaveDisplayCurrency("CNY"); err == nil {
		t.Fatal("malformed config was saved")
	}
	for i, p := range locals {
		if len(p.events) != before[i] {
			t.Fatal("failed persistence announced a currency change")
		}
		check(p, "USD")
	}
	previous := locals[1]
	replacement := newProbe()
	if err := os.WriteFile(config.UserConfigPath(), []byte("[billing]\ndisplay_currency = \"USD\"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := group.Bind(previous.controller, replacement.controller, nil); err != nil {
		t.Fatal(err)
	}
	locals[1] = replacement
	if len(replacement.events) != 0 {
		t.Fatal("replacement repeated a visible notice")
	}
	check(replacement, "USD")
	if err := previous.controller.SaveDisplayCurrency("CNY"); !errors.Is(err, ErrDisplayCurrencyRetired) {
		t.Fatalf("retired save = %v", err)
	}
	previousCount := len(previous.events)
	locals[2].controller.Close()
	if err := locals[2].controller.SaveDisplayCurrency("CNY"); !errors.Is(err, ErrDisplayCurrencyRetired) {
		t.Fatalf("closed save = %v", err)
	}
	closedCount := len(locals[2].events)
	notice.Detail = "CNY"
	emit(notice)
	if len(previous.events) != previousCount || len(locals[2].events) != closedCount {
		t.Fatal("retired controller received a currency notice")
	}
	check(locals[0], "CNY")
	check(replacement, "CNY")
}
