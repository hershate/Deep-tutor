package tutor

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"path"
	"strings"
	"time"
	"unicode/utf8"
)

// notesio.go — portable notes ZIP export/import, Deep-tutor iter 286/292:
// human-readable standalone .md files (one per note plus an index.md
// manifest) out, .md files or .zip archives of them back in.

// Note quota and size ceilings, mirroring Deep-tutor's note limits.
const (
	noteQuota            = 2000
	noteTitleMax         = 256
	noteBodyMax          = 20000
	noteImportEntryMax   = 100000 // raw bytes per imported entry
	noteImportTotalBytes = 5 << 20
)

// NoteImportFile is one uploaded file: a .md note or a .zip of notes.
type NoteImportFile struct {
	Name string
	Data []byte
}

// NoteImportResult reports what an import did; QuotaFull means the note
// ceiling stopped further imports, not that the request failed.
type NoteImportResult struct {
	Imported  int  `json:"imported"`
	Skipped   int  `json:"skipped"`
	QuotaFull bool `json:"quota_full"`
}

// safeNoteStem turns a note title into a zip-safe file stem; empty when
// nothing usable remains.
func safeNoteStem(title string) string {
	repl := strings.NewReplacer(
		"/", "‑", "\\", "‑", ":", "‑", "*", "‑", "?", "‑", "\"", "‑",
		"<", "‑", ">", "‑", "|", "‑", "\n", " ", "\r", " ", "\t", " ",
	)
	stem := strings.TrimSpace(repl.Replace(title))
	if utf8.RuneCountInString(stem) > 120 {
		stem = string([]rune(stem)[:120])
	}
	return stem
}

// NoteMarkdown renders one note as a standalone .md document, the exact
// inverse of splitMarkdownTitle.
func NoteMarkdown(n Note) string {
	title := strings.TrimSpace(n.Title)
	if title == "" {
		title = "无题"
	}
	body := strings.TrimRight(n.Body, " \n\t\r")
	if body != "" {
		return "# " + title + "\n\n" + body + "\n"
	}
	return "# " + title + "\n"
}

// ExportNotesZip packs every note into a portable .md archive held in
// memory. A note set can be a hundred megabytes of text, so a caller that
// exports a large one should use ExportNotesZipTo and stream the archive
// instead of holding a second copy of it in the heap.
func ExportNotesZip(notes []Note, now time.Time) ([]byte, error) {
	var buf bytes.Buffer
	if err := ExportNotesZipTo(&buf, notes, now); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

// ExportNotesZipTo writes every note to w as a portable .md archive: one
// file per note (duplicate stems get the id prefixed) plus an index.md
// manifest, Deep-tutor's portable export shape. The caller owns the writer,
// so the archive is never held whole — the same discipline the v1 export
// uses (iter 396-B2).
func ExportNotesZipTo(w io.Writer, notes []Note, now time.Time) error {
	if len(notes) == 0 {
		return ErrNotFound
	}
	zw := zip.NewWriter(w)
	used := map[string]bool{}
	index := []string{
		"# 笔记导出清单", "",
		"- 导出时间：" + now.Format("2006-01-02 15:04:05"),
		fmt.Sprintf("- 笔记数：%d", len(notes)), "",
		"| 文件 | 标题 | 更新时间 |", "| --- | --- | --- |",
	}
	for _, n := range notes {
		stem := safeNoteStem(n.Title)
		if stem == "" {
			stem = n.ID
		}
		name := stem + ".md"
		if used[name] {
			name = n.ID + "-" + stem + ".md"
		}
		used[name] = true
		entry, err := zw.Create(name)
		if err != nil {
			return err
		}
		if _, err := entry.Write([]byte(NoteMarkdown(n))); err != nil {
			return err
		}
		index = append(index, fmt.Sprintf("| %s | %s | %s |",
			name, strings.ReplaceAll(strings.TrimSpace(n.Title), "|", "/"),
			n.UpdatedAt.Format("2006-01-02 15:04:05")))
	}
	index = append(index, "")
	manifest, err := zw.Create("index.md")
	if err != nil {
		return err
	}
	if _, err := manifest.Write([]byte(strings.Join(index, "\n"))); err != nil {
		return err
	}
	return zw.Close()
}

// splitMarkdownTitle pulls (title, body) from note markdown: a leading
// "# " line is the title (the export format), otherwise the fallback — the
// file's basename — names it and the whole text stays as the body.
func splitMarkdownTitle(text, fallbackTitle string) (string, string) {
	lines := strings.SplitN(text, "\n", 2)
	first := strings.TrimSpace(lines[0])
	if strings.HasPrefix(first, "# ") {
		title := strings.TrimSpace(first[2:])
		if utf8.RuneCountInString(title) > noteTitleMax {
			title = string([]rune(title)[:noteTitleMax])
		}
		body := ""
		if len(lines) > 1 {
			body = strings.TrimSpace(lines[1])
		}
		return title, body
	}
	title := fallbackTitle
	if utf8.RuneCountInString(title) > noteTitleMax {
		title = string([]rune(title)[:noteTitleMax])
	}
	return title, strings.TrimSpace(text)
}

// clampRunes truncates s to at most max runes.
func clampRunes(s string, max int) string {
	if utf8.RuneCountInString(s) <= max {
		return s
	}
	return string([]rune(s)[:max])
}

// importState carries one import's counters past the consume closure.
type importState struct {
	store      *Store
	existing   int
	imported   int
	skipped    int
	quotaFull  bool
	totalBytes int
}

// consume imports one candidate entry, enforcing the per-entry and total
// byte ceilings and the note quota.
func (st *importState) consume(name, text string) {
	if st.quotaFull || st.existing+st.imported >= noteQuota {
		st.quotaFull = true
		st.skipped++
		return
	}
	if utf8.RuneCountInString(text) > noteBodyMax {
		text = clampRunes(text, noteBodyMax)
	}
	title, body := splitMarkdownTitle(text, noteStemOf(name))
	if strings.TrimSpace(title) == "" && strings.TrimSpace(body) == "" {
		st.skipped++
		return
	}
	note := &Note{Title: title, Body: body}
	if err := st.store.SaveNoteGuarded(note); err != nil {
		st.skipped++
		return
	}
	st.imported++
}

// noteStemOf is the import's title fallback: the file's basename without
// its extension.
func noteStemOf(name string) string {
	base := path.Base(strings.ReplaceAll(name, "\\", "/"))
	return strings.TrimSuffix(base, path.Ext(base))
}

// ImportNotes imports .md files and .zip archives of .md files as free
// notes. Non-.md entries are silently ignored, undecodable or oversized
// entries count as skipped, and the whole request respects the note quota
// and a total-bytes ceiling — the same surface as Deep-tutor's import.
func ImportNotes(store *Store, files []NoteImportFile) (NoteImportResult, error) {
	existing := 0
	if notes, err := store.Notes(); err == nil {
		existing = len(notes)
	}
	st := &importState{store: store, existing: existing}
	for _, f := range files {
		name := strings.ToLower(strings.TrimSpace(f.Name))
		switch {
		case strings.HasSuffix(name, ".md") || strings.HasSuffix(name, ".markdown"):
			st.totalBytes += len(f.Data)
			if st.totalBytes > noteImportTotalBytes || len(f.Data) > noteImportEntryMax {
				st.skipped++
				continue
			}
			st.consume(f.Name, string(f.Data))
		case strings.HasSuffix(name, ".zip"):
			if err := importNoteZip(st, f.Data); err != nil {
				st.skipped++
			}
		default:
			st.skipped++
		}
	}
	return NoteImportResult{Imported: st.imported, Skipped: st.skipped, QuotaFull: st.quotaFull}, nil
}

// importNoteZip walks a zip archive importing every .md entry; other entries
// are ignored. Zip-bomb guards come from openZipGuarded, as with documents.
func importNoteZip(st *importState, data []byte) error {
	zr, err := openZipGuarded(data)
	if err != nil {
		return err
	}
	for _, f := range zr.File {
		if f.FileInfo().IsDir() || !strings.HasSuffix(strings.ToLower(f.Name), ".md") {
			continue
		}
		// Skip our own export manifest so an exported archive re-imports as
		// exactly the notes it left with (Deep-tutor re-imports it as a
		// note; we keep the round-trip clean instead).
		if f.Name == "index.md" {
			continue
		}
		if f.UncompressedSize64 > noteImportEntryMax {
			st.skipped++
			continue
		}
		rc, err := f.Open()
		if err != nil {
			st.skipped++
			continue
		}
		raw, err := io.ReadAll(io.LimitReader(rc, noteImportEntryMax+1))
		rc.Close()
		if err != nil || len(raw) > noteImportEntryMax {
			st.skipped++
			continue
		}
		st.totalBytes += len(raw)
		if st.totalBytes > noteImportTotalBytes {
			st.skipped++
			continue
		}
		st.consume(f.Name, string(raw))
	}
	return nil
}
