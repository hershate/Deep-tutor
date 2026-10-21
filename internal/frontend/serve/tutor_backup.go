package serve

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"reasonix/internal/runtime/tutor"
)

// The decoded cap the import path enforces (Deep Tutor's 256MB); the reader
// cap above it leaves room for base64 inflation.
const backupReaderCap = 360 << 20

// tutorBackupDecodedCap is the decoded-size cap the import path enforces.
// A var so the test can lower it instead of allocating a 256MB payload.
var tutorBackupDecodedCap = tutor.BackupMaxBytes

// tutorBackupMu serializes the backup operations that hold a whole-store
// document in memory. The import path parses the entire backup before it
// writes anything, so N concurrent imports would each hold a full copy of the
// store (base64 envelope + decoded document) — the same reason Deep Tutor's
// /api/backup takes _RESTORE_LOCK before it reads the upload. A non-blocking
// acquire turns the second caller into a 409 instead of queueing another peak.
//
// The lock is taken before the body is read, not after: the memory peak lives
// in the read-and-parse step, so a lock acquired later would not bound it.
var tutorBackupMu sync.Mutex

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
	// The export assembles the whole store in memory, so it shares the
	// backup lock: an export running beside an import would hold two full
	// copies of the store at once.
	if !tutorBackupLock(w) {
		return
	}
	defer tutorBackupMu.Unlock()
	doc, err := svc.BuildBackup()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	tmp, err := os.CreateTemp("", "tutor-backup-*.json")
	if err != nil {
		tutorFail(w, err)
		return
	}
	defer os.Remove(tmp.Name())
	if err := tutor.WriteBackup(tmp, doc); err != nil {
		tmp.Close()
		tutorFail(w, err)
		return
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		tmp.Close()
		tutorFail(w, err)
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
	encoded := strings.TrimSpace(body.ContentBase64)
	// Size check **before** decoding: the decoded copy is another ~256MB of
	// memory, and an oversized payload must not cost it. DecodedLen is an
	// upper bound (it ignores padding), so the check is conservative.
	if decoded := base64.StdEncoding.DecodedLen(len(encoded)); decoded > tutorBackupDecodedCap {
		refuse(w, http.StatusRequestEntityTooLarge, "tutor.backup_too_large", "the backup file is too large", nil)
		return nil, false
	}
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		refuse(w, http.StatusBadRequest, "tutor.bad_backup", "the file is not a valid backup document", nil)
		return nil, false
	}
	return raw, true
}

// tutorBackupLock takes the process-wide backup lock without blocking. The
// caller must hold it before reading the request body.
func tutorBackupLock(w http.ResponseWriter) bool {
	if tutorBackupMu.TryLock() {
		return true
	}
	refuse(w, http.StatusConflict, "tutor.backup_busy", "another backup operation is in progress, retry shortly", nil)
	return false
}

func (s *Server) tutorBackupPreview(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if !tutorBackupLock(w) {
		return
	}
	defer tutorBackupMu.Unlock()
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
	if !tutorBackupLock(w) {
		return
	}
	defer tutorBackupMu.Unlock()
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
