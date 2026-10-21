package control

// ReceiptSettings is whether the end-of-turn delivery receipt is drawn as a
// card. The user file holds it, so Studio and the terminal answer alike.
type ReceiptSettings struct {
	Show bool   `json:"show"`
	Path string `json:"path"`
}

func (c *Controller) ReceiptSettings() ReceiptSettings {
	path := c.roots.UserConfigPath()
	return ReceiptSettings{Show: c.roots.LoadForEdit(path).UI.ShowReceipt, Path: path}
}

// SaveReceiptSettings persists the choice. Nothing is rebuilt: the receipt is
// recorded either way and only its drawing depends on this.
func (c *Controller) SaveReceiptSettings(show bool) error {
	unlock := c.roots.LockUserConfigEdits()
	defer unlock()
	path := c.roots.UserConfigPath()
	cfg := c.roots.LoadForEdit(path)
	cfg.UI.ShowReceipt = show
	return cfg.SaveTo(path)
}
