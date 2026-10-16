package control

import (
	"errors"
	"fmt"

	"reasonix/internal/base/i18n"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/pricing"
)

// ErrDisplayCurrencyInvalid is a mode that is none of auto, CNY or USD.
var ErrDisplayCurrencyInvalid = errors.New("display currency")

// DisplayCurrencySettings is the user's stored display-currency preference:
// "auto" follows the wallet, otherwise the pinned currency code.
type DisplayCurrencySettings struct {
	Mode string `json:"mode"`
	Path string `json:"path"`
}

func (c *Controller) DisplayCurrencySettings() DisplayCurrencySettings {
	path := config.UserConfigPath()
	cfg := config.LoadForEdit(path)
	return DisplayCurrencySettings{Mode: currencyDisplay(cfg.DisplayCurrencyPref()), Path: path}
}

// SaveDisplayCurrency stores the preference and announces it, which is what
// rebinds the quote sink and every frontend ledger without a rebuild.
func (c *Controller) SaveDisplayCurrency(mode string) error {
	pref, err := ParseDisplayCurrency(mode)
	if err != nil {
		return err
	}
	group := c.currencyGroup()
	if group != nil {
		group.mu.Lock()
		defer group.mu.Unlock()
		if _, live := group.members[c]; !live {
			return ErrDisplayCurrencyRetired
		}
	}
	c.mu.Lock()
	closed := c.gate.closed
	c.mu.Unlock()
	if closed {
		return ErrDisplayCurrencyRetired
	}
	path := config.UserConfigPath()
	if path == "" {
		return errors.New("cannot resolve user config path")
	}
	var resolved string
	err = config.EditConfigFile(path, func(cfg *config.Config) error {
		if err := cfg.SetDisplayCurrency(pref); err != nil {
			return err
		}
		resolved = cfg.ResolveDisplayCurrency()
		return nil
	})
	if err != nil {
		return err
	}
	c.mu.Lock()
	c.display.walletCurrency = ""
	c.mu.Unlock()
	group.emitLocked(c, event.Event{Kind: event.Notice, Level: event.LevelInfo, Code: event.NoticeCodeDisplayCurrency,
		Text: fmt.Sprintf(i18n.M.CurrencyChangedFmt, currencyDisplay(pref), resolved), Detail: pref})
	return nil
}

// AdoptWalletCurrency values the session in the wallet's currency while the
// user has not chosen one. It is announced the way a chosen currency is, so the
// quotes and every frontend move together; nothing is written to the config.
func (c *Controller) AdoptWalletCurrency(code string) {
	code = pricing.NormalizeCurrency(code)
	if code == "" || config.LoadForEdit(config.UserConfigPath()).DisplayCurrencyPref() != "" {
		return
	}
	c.mu.Lock()
	same := c.display.walletCurrency == code
	c.display.walletCurrency = code
	c.mu.Unlock()
	if same {
		return
	}
	group := c.currencyGroup()
	if group != nil {
		group.mu.Lock()
		defer group.mu.Unlock()
	}
	group.emitLocked(c, event.Event{Kind: event.Notice, Level: event.LevelInfo, Audience: event.NoticeAudienceOperator,
		Code: event.NoticeCodeDisplayCurrency, Text: fmt.Sprintf(i18n.M.CurrencyChangedFmt, currencyDisplay(""), code), Detail: code})
}
