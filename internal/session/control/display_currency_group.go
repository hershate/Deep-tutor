package control

import (
	"errors"
	"log/slog"
	"sync"

	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
)

// ErrDisplayCurrencyRetired refuses a save from a replaced or closed controller.
var ErrDisplayCurrencyRetired = errors.New("display currency controller is retired")

// DisplayCurrencyGroup binds the local controllers that share one persisted
// preference. Its zero value is ready to use; remote controllers never join it.
type DisplayCurrencyGroup struct {
	mu      sync.Mutex
	members map[*Controller]struct{}
}

// Bind replaces a live generation and quietly reconciles its quote and frontend
// projections with canonical configuration before the host publishes it.
func (g *DisplayCurrencyGroup) Bind(previous, next *Controller, display func(string)) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if previous != nil {
		if _, live := g.members[previous]; !live {
			return ErrDisplayCurrencyRetired
		}
	}
	if next == nil {
		return errors.New("display currency controller is unavailable")
	}
	cfg, err := config.LoadForEditReadOnlyStrict(config.UserConfigPath())
	next.mu.Lock()
	if next.gate.closed {
		next.mu.Unlock()
		return ErrDisplayCurrencyRetired
	}
	next.display.currencyGroup = g
	quote := next.display.quoteContext
	next.mu.Unlock()
	delete(g.members, previous)
	if g.members == nil {
		g.members = make(map[*Controller]struct{})
	}
	g.members[next] = struct{}{}
	if err != nil {
		slog.Warn("controller: load display currency for publication; keeping current projections", "err", err)
		return nil
	}
	currency := cfg.DisplayCurrencyPref()
	quote.SetDisplay(currency)
	if display != nil {
		display(currency)
	}
	return nil
}

func (c *Controller) currencySink() event.Sink {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.sink
}

func (c *Controller) currencyGroup() *DisplayCurrencyGroup {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.display.currencyGroup
}

func (c *Controller) leaveCurrencyGroup() {
	if g := c.currencyGroup(); g != nil {
		g.mu.Lock()
		delete(g.members, c)
		g.mu.Unlock()
	}
}

func (g *DisplayCurrencyGroup) emitLocked(c *Controller, notice event.Event) {
	if g == nil {
		c.currencySink().Emit(notice)
		return
	}
	for peer := range g.members {
		peer.currencySink().Emit(notice)
	}
}
