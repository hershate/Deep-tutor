package tutor

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// A failed import must leave the store exactly as it was. The refusal tells
// the caller "nothing was changed", so every row the run created has to be
// rolled back — the notes the run wrote before the failure included.
func TestRestoreBackupFailedImportRollsBackNotes(t *testing.T) {
	dst := newShareService(t)
	// The temp dir cleanup is strict: a stray file (Windows may leave an
	// atomic-write temp behind when a write loses a race with the scanner)
	// would fail the whole test instead of the assertion under test.
	t.Cleanup(func() { _ = os.RemoveAll(dst.store.notesDir()) })

	// Block the knowledge-base parsed directory with a regular file: the
	// knowledge-base restore then fails after the notes were already written.
	kbDir := dst.store.kbDir()
	if err := os.MkdirAll(kbDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(kbDir, "parsed"), []byte("block"), 0o644); err != nil {
		t.Fatal(err)
	}

	doc := &BackupDoc{
		Format:   backupFormat,
		Version:  backupVersion,
		Sessions: []BackupSession{},
		Notes:    []BackupNote{{Title: "会回滚的笔记", Body: "正文"}},
		Kb:       []BackupKbDoc{{Title: "库文档", Kind: MaterialText, ParsedText: "知识库正文"}},
	}
	if _, err := dst.RestoreBackup(doc); !errors.Is(err, ErrImportFailed) {
		t.Fatalf("import should fail: %v", err)
	}

	notes, err := dst.store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	if len(notes) != 0 {
		t.Fatalf("failed import left notes behind: %+v", notes)
	}
}

// A successful import keeps the notes it wrote.
func TestRestoreBackupKeepsNotesOnSuccess(t *testing.T) {
	dst := newShareService(t)

	doc := &BackupDoc{
		Format:   backupFormat,
		Version:  backupVersion,
		Sessions: []BackupSession{},
		Notes:    []BackupNote{{Title: "保留的笔记", Body: "正文"}},
		Kb:       []BackupKbDoc{},
	}
	summary, err := dst.RestoreBackup(doc)
	if err != nil {
		t.Fatal(err)
	}
	if summary["notes"] != 1 {
		t.Fatalf("summary: %v", summary)
	}
	notes, err := dst.store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	if len(notes) != 1 || notes[0].Title != "保留的笔记" {
		t.Fatalf("notes after import: %+v", notes)
	}
}
