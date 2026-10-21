package control

// ChangedFilesSettings is whether Studio draws the changed-files count as a
// pill above the composer. The user file holds it; the change list itself is
// unaffected.
type ChangedFilesSettings struct {
	Show bool   `json:"show"`
	Path string `json:"path"`
}

func (c *Controller) ChangedFilesSettings() ChangedFilesSettings {
	path := c.roots.UserConfigPath()
	return ChangedFilesSettings{Show: c.roots.LoadForEdit(path).UI.ShowChangedFiles, Path: path}
}

// SaveChangedFilesSettings needs no rebuild: only the drawing depends on it.
func (c *Controller) SaveChangedFilesSettings(show bool) error {
	unlock := c.roots.LockUserConfigEdits()
	defer unlock()
	path := c.roots.UserConfigPath()
	cfg := c.roots.LoadForEdit(path)
	cfg.UI.ShowChangedFiles = show
	return cfg.SaveTo(path)
}
