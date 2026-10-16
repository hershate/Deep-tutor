package tutor

import (
	"archive/zip"
	"fmt"
	"io"
	"log/slog"
	"os"
	"slices"
	"strings"
	"time"
)

// kbio.go — KB bulk exits: the ZIP export (index manifest + originals/ +
// parsed/) and one-click plan generation from library documents.

// KbExportZip writes the selected documents to a temp file and returns its
// path; the serve layer streams it and removes it. A spooled temp file, not
// an in-memory buffer: 200 originals can dwarf any honest RAM budget.
// Unknown ids in the filter are skipped silently — an export is a bulk
// convenience, not a probe. An empty selection refuses.
func (s *Service) KbExportZip(ids []string) (string, int, error) {
	docs, err := s.store.KbDocs()
	if err != nil {
		return "", 0, err
	}
	wanted := map[string]bool{}
	explicit := len(ids) > 0
	for _, id := range ids {
		wanted[id] = true
	}
	selected := make([]KbDoc, 0, len(docs))
	// oldest first, DT's id-asc order
	for _, doc := range slices.Backward(docs) {
		if explicit && !wanted[doc.ID] {
			continue
		}
		selected = append(selected, doc)
	}
	if len(selected) == 0 {
		return "", 0, ErrKbExportEmpty
	}

	tmp, err := os.CreateTemp("", "kb-export-*.zip")
	if err != nil {
		return "", 0, err
	}
	path := tmp.Name()
	cleanup := func() { _ = os.Remove(path) }
	zw := zip.NewWriter(tmp)
	writeErr := func(err error) (string, int, error) {
		_ = zw.Close()
		_ = tmp.Close()
		cleanup()
		return "", 0, err
	}
	manifest := kbExportManifest(selected)
	if err := writeZipEntry(zw, "index.md", strings.NewReader(manifest)); err != nil {
		return writeErr(err)
	}
	for i := range selected {
		d := &selected[i]
		stem := d.ID + "-" + kbExportStem(d)
		if d.HasOriginal {
			src := s.store.kbOriginalPath(d)
			if err := writeZipFile(zw, "originals/"+stem+"."+kbOriginalExt(d), src); err != nil {
				continue // missing original: log-and-skip, DT's tolerance
			}
		}
		if d.Status == "parsed" {
			_ = writeZipFile(zw, "parsed/"+stem+".txt", s.store.kbParsedPath(d.ID))
		}
	}
	if err := zw.Close(); err != nil {
		cleanup()
		return "", 0, err
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return "", 0, err
	}
	return path, len(selected), nil
}

func writeZipEntry(zw *zip.Writer, name string, r io.Reader) error {
	w, err := zw.Create(name)
	if err != nil {
		return err
	}
	_, err = io.Copy(w, r)
	return err
}

func writeZipFile(zw *zip.Writer, name, path string) error {
	src, err := os.Open(path)
	if err != nil {
		slog.Warn("tutor: kb export file missing", "entry", name, "path", path)
		return err
	}
	defer src.Close()
	return writeZipEntry(zw, name, src)
}

// kbOriginalExt picks the archive suffix for an original, .bin when the row
// carries none.
func kbOriginalExt(d *KbDoc) string {
	if d.Ext == "" || !safeExt.MatchString(d.Ext) {
		return "bin"
	}
	return d.Ext
}

// kbExportStem sanitizes a title for the archive, dropping a trailing
// extension so entries never double up (a.md -> a.md.md).
func kbExportStem(d *KbDoc) string {
	title := d.Title
	if d.Ext != "" {
		if suffix := "." + strings.ToLower(d.Ext); strings.HasSuffix(strings.ToLower(title), suffix) {
			title = title[:len(title)-len(suffix)]
		}
	}
	stem := safeNoteStem(title)
	if stem == "" {
		stem = "doc-" + d.ID
	}
	return stem
}

func kbExportManifest(docs []KbDoc) string {
	var sb strings.Builder
	sb.WriteString("# 知识库导出清单\n\n")
	fmt.Fprintf(&sb, "- 导出时间：%s\n", time.Now().Format("2006-01-02 15:04:05"))
	fmt.Fprintf(&sb, "- 文档数：%d\n\n", len(docs))
	sb.WriteString("| ID | 标题 | 类型 | 扩展名 | 解析字数 | 原件 | 创建时间 |\n")
	sb.WriteString("| --- | --- | --- | --- | --- | --- | --- |\n")
	for i := range docs {
		d := &docs[i]
		original := "无"
		if d.HasOriginal {
			original = "有"
		}
		ext := d.Ext
		if ext == "" {
			ext = "-"
		}
		fmt.Fprintf(&sb, "| %s | %s | %s | %s | %d | %s | %s |\n",
			d.ID, strings.ReplaceAll(d.Title, "|", "/"), d.Kind, ext,
			d.Chars, original, d.CreatedAt.Format("2006-01-02 15:04:05"))
	}
	sb.WriteString("\n")
	return sb.String()
}

// KbGenerateFromDocs builds a fresh generation session seeded with the
// selected documents' parsed text as session materials — the zero-modification
// reuse of the pipeline's materials injection. Every document must already be
// parsed; any failure tears the whole session down so nothing half-seeded
// survives.
func (s *Service) KbGenerateFromDocs(ids []string, title string) (string, int, error) {
	ordered := []string{}
	seen := map[string]bool{}
	for _, id := range ids {
		if !seen[id] {
			seen[id] = true
			ordered = append(ordered, id)
		}
	}
	if len(ordered) == 0 {
		return "", 0, ErrKbEmpty
	}
	docs := map[string]*KbDoc{}
	all, err := s.store.KbDocs()
	if err != nil {
		return "", 0, err
	}
	for i := range all {
		docs[all[i].ID] = &all[i]
	}
	for _, id := range ordered {
		d, ok := docs[id]
		if !ok {
			return "", 0, fmt.Errorf("%w: document %s does not exist", ErrNotFound, id)
		}
		if d.Status != "parsed" {
			return "", 0, fmt.Errorf("%w: document %s is not parsed yet", ErrNotFound, id)
		}
	}
	if title = strings.TrimSpace(title); title == "" {
		title = "知识库：" + docs[ordered[0]].Title
	}
	sess, err := s.store.CreateSession(clampRunes(title, 256))
	if err != nil {
		return "", 0, err
	}
	count := 0
	for _, id := range ordered {
		d := docs[id]
		text, err := s.store.ReadKbParsed(id)
		if err != nil {
			_ = s.store.DeleteSession(sess.ID)
			return "", 0, fmt.Errorf("%w: document %s parsed text is missing", ErrNotFound, id)
		}
		m := &Material{
			ID: newID(), SessionID: sess.ID, Kind: d.Kind, Source: d.Source,
			Status: "parsed", ParseMode: d.ParseMode, Chars: len([]rune(text)),
			Meta: map[string]string{"kb_document_id": d.ID}, CreatedAt: s.opt.now(),
		}
		if err := s.store.WriteMaterialParsed(sess.ID, m.ID, text); err != nil {
			_ = s.store.DeleteSession(sess.ID)
			return "", 0, err
		}
		if err := s.store.SaveMaterial(m); err != nil {
			_ = s.store.DeleteSession(sess.ID)
			return "", 0, err
		}
		count++
	}
	return sess.ID, count, nil
}
