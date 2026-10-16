package control

import (
	"os"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
)

func currencyNotices(events []event.Event) []event.Event {
	var out []event.Event
	for _, e := range events {
		if e.Kind == event.Notice && e.Code == event.NoticeCodeDisplayCurrency {
			out = append(out, e)
		}
	}
	return out
}

func TestAWalletCurrencyIsAnnouncedOnceWhileTheUserLeftItAutomatic(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	var seen []event.Event
	c := New(Options{Sink: event.FuncSink(func(e event.Event) { seen = append(seen, e) }), QuoteContext: &event.QuoteContext{}})
	t.Cleanup(c.Close)

	c.AdoptWalletCurrency("CNY")
	got := currencyNotices(seen)
	if len(got) != 1 || got[0].Detail != "CNY" || got[0].Audience != event.NoticeAudienceOperator {
		t.Fatalf("notices = %+v, want one operator notice naming CNY", got)
	}
	c.AdoptWalletCurrency("CNY")
	if len(currencyNotices(seen)) != 1 {
		t.Fatal("the same wallet currency was announced twice")
	}
	if raw, err := os.ReadFile(config.UserConfigPath()); err == nil && len(raw) > 0 {
		t.Fatalf("adopting a wallet currency wrote the config: %q", raw)
	}
}

func TestAChosenCurrencyIsNotOverriddenByTheWallet(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	if err := os.WriteFile(config.UserConfigPath(), []byte("[billing]\ndisplay_currency = \"USD\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var seen []event.Event
	c := New(Options{Sink: event.FuncSink(func(e event.Event) { seen = append(seen, e) }), QuoteContext: &event.QuoteContext{}})
	t.Cleanup(c.Close)
	c.AdoptWalletCurrency("CNY")
	if got := currencyNotices(seen); len(got) != 0 {
		t.Fatalf("a wallet overrode the user's chosen currency: %+v", got)
	}
}
