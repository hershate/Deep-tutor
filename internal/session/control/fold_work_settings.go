package control

// FoldWorkSettings is whether Studio folds a turn's intermediate work under one
// line. The user file holds it; the transcript itself is unaffected.
type FoldWorkSettings struct {
	Fold bool   `json:"fold"`
	Path string `json:"path"`
}

func (c *Controller) FoldWorkSettings() FoldWorkSettings {
	path := c.roots.UserConfigPath()
	return FoldWorkSettings{Fold: c.roots.LoadForEdit(path).UI.FoldTurnWork, Path: path}
}

// SaveFoldWorkSettings needs no rebuild: only the drawing depends on it.
func (c *Controller) SaveFoldWorkSettings(fold bool) error {
	unlock := c.roots.LockUserConfigEdits()
	defer unlock()
	path := c.roots.UserConfigPath()
	cfg := c.roots.LoadForEdit(path)
	cfg.UI.FoldTurnWork = fold
	return cfg.SaveTo(path)
}
