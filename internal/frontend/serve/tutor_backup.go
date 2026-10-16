package serve

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"reasonix/internal/runtime/tutor"
)

// The decoded cap the import path enforces (Deep Tutor's 256MB); the reader
// cap above it leaves room for base64 inflation.
const backupReaderCap = 360 << 20

func (s *Server) registerTutorBackupRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /tutor/backup/export", s.tutorBackupExport)
	mux.HandleFunc("POST /tutor/backup/preview", s.tutorBackupPreview)
	mux.HandleFunc("POST /tutor/backup/import", s.tutorBackupImport)
}

// tutorBackupExport streams the whole-store backup as a JSON attachment. The
// document is spooled to a temp file first so a huge store never sits twice
// in memory, the knowledge-base export's discipline.
func (s *Server) tutorBackupExport(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	doc, err := svc.BuildBackup()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	tmp, err := os.CreateTemp("", "tutor-backup-*.json")
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	defer os.Remove(tmp.Name())
	if err := tutor.WriteBackup(tmp, doc); err != nil {
		tmp.Close()
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		tmp.Close()
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="reasonix-tutor-backup-`+time.Now().Format("20060102-150405")+`.json"`)
	w.Header().Set("Cache-Control", "no-store")
	if _, err := io.Copy(w, tmp); err != nil {
		// The header is already out; the partial download is what remains.
		return
	}
	tmp.Close()
}

// tutorBackupBody decodes the base64-JSON upload envelope shared by preview
// and import, enforcing the decoded-size cap.
func tutorBackupBody(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	var body struct {
		ContentBase64 string `json:"content_base64"`
	}
	dec := json.NewDecoder(http.MaxBytesReader(w, r.Body, backupReaderCap))
	if err := dec.Decode(&body); err != nil || strings.TrimSpace(body.ContentBase64) == "" {
		badBody(w)
		return nil, false
	}
	raw, err := base64.StdEncoding.DecodeString(body.ContentBase64)
	if err != nil {
		refuse(w, http.StatusBadRequest, "tutor.bad_backup", "the file is not a valid backup document", nil)
		return nil, false
	}
	return raw, true
}

func (s *Server) tutorBackupPreview(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	raw, ok := tutorBackupBody(w, r)
	if !ok {
		return
	}
	doc, err := tutor.ParseBackup(raw)
	if err != nil {
		tutorBackupRefuse(w, err)
		return
	}
	writeJSON(w, tutor.BackupPreview(doc))
}

func (s *Server) tutorBackupImport(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	raw, ok := tutorBackupBody(w, r)
	if !ok {
		return
	}
	doc, err := tutor.ParseBackup(raw)
	if err != nil {
		tutorBackupRefuse(w, err)
		return
	}
	summary, err := svc.RestoreBackup(doc)
	if err != nil {
		tutorBackupRefuse(w, err)
		return
	}
	writeJSON(w, summary)
}

func tutorBackupRefuse(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, tutor.ErrBadBackup):
		refuse(w, http.StatusBadRequest, "tutor.bad_backup", "the file is not a valid backup document", nil)
	case errors.Is(err, tutor.ErrBackupTooLarge):
		refuse(w, http.StatusRequestEntityTooLarge, "tutor.backup_too_large", "the backup file is too large", nil)
	case errors.Is(err, tutor.ErrImportFailed):
		refuse(w, http.StatusInternalServerError, "tutor.import_failed", "the import failed and was rolled back; nothing was changed", nil)
	default:
		tutorRefuse(w, err)
	}
}
