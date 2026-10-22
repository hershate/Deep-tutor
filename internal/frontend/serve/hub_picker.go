package serve

import (
	"encoding/json"
	"errors"
	"net/http"
	"sync"
)

var errFolderPickerUnsupported = errors.New("native folder picker is unavailable")

// folderPickerSlot is the one dialog this host may show at a time. A picker
// waits for a person to choose a folder, and the process behind it stays alive
// until they answer; a client that asks twice gets two dialogs and two
// processes, and nobody is served by that.
var folderPickerSlot = struct {
	mu   sync.Mutex
	open bool
}{}

// claimFolderPicker takes the one dialog slot and reports whether it got it.
func claimFolderPicker() bool {
	folderPickerSlot.mu.Lock()
	defer folderPickerSlot.mu.Unlock()
	if folderPickerSlot.open {
		return false
	}
	folderPickerSlot.open = true
	return true
}

// releaseFolderPicker gives the slot back.
func releaseFolderPicker() {
	folderPickerSlot.mu.Lock()
	folderPickerSlot.open = false
	folderPickerSlot.mu.Unlock()
}

// Kept as a variable so the transport contract can be tested without opening
// a real operating-system dialog. The implementation is selected per OS.
var openLocalFolderPicker = pickLocalFolder

// Kept separately from the picker itself so the capability route never opens a
// dialog, and tests can describe a headless host without changing the OS.
var folderPickerAvailable = pickLocalFolderAvailable

func (h *Hub) pickLocalFolderHTTP(w http.ResponseWriter, r *http.Request) {
	if !claimFolderPicker() {
		refuse(w, http.StatusConflict, "picker.busy", "another folder picker is already open", nil)
		return
	}
	defer releaseFolderPicker()
	var req struct {
		StartIn string `json:"startIn"`
	}
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&req)
	}
	path, err := openLocalFolderPicker(r.Context(), req.StartIn)
	if errors.Is(err, errFolderPickerUnsupported) {
		refuse(w, http.StatusNotImplemented, "picker.unsupported", err.Error(), nil)
		return
	}
	if err != nil {
		refuse(w, http.StatusInternalServerError, "picker.failed", err.Error(), nil)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(struct {
		Path string `json:"path"`
	}{Path: path})
}

// hostCapabilities tells the page which host-only actions it may offer. The
// path-based workspace API is a kernel capability, so it stays available even
// when the native picker cannot run.
func (h *Hub) hostCapabilities(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, struct {
		PickFolder   bool `json:"pickFolder"`
		AddWorkspace bool `json:"addWorkspace"`
	}{PickFolder: folderPickerAvailable(), AddWorkspace: true})
}
