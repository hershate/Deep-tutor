package control

import "reasonix/internal/contract/config"

// ChangedFilesSettings is whether Studio draws the changed-files count as a
// pill above the composer. The user file holds it; the change list itself is
// unaffected.
type ChangedFilesSettings struct {
	Show bool   `json:"show"`
	Path string `json:"path"`
}

func (c *Controller) ChangedFilesSettings() ChangedFilesSettings {
	path := config.UserConfigPath()
	return ChangedFilesSettings{Show: config.LoadForEdit(path).UI.ShowChangedFiles, Path: path}
}

// SaveChangedFilesSettings needs no rebuild: only the drawing depends on it.
func (c *Controller) SaveChangedFilesSettings(show bool) error {
	unlock := config.LockUserConfigEdits()
	defer unlock()
	path := config.UserConfigPath()
	cfg := config.LoadForEdit(path)
	cfg.UI.ShowChangedFiles = show
	return cfg.SaveTo(path)
}
