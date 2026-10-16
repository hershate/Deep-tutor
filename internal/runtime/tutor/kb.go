package tutor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode"
)

// kb.go — the knowledge base, Deep-tutor's /kb surface: a document
// library independent of plan sessions, flowing upload/confirm/parse
// (text/url straight to parsed) with dedup, quota, and content search.

// KbMaxDocs caps the library, Deep-tutor's per-user quota.
const KbMaxDocs = 200

// parsedScanMaxDocs bounds content search: the newest N parsed documents are
// scanned; older ones stay title-searchable only. No index is built — the
// honest boundary Deep-tutor chose over an FTS copy of every parse.
const parsedScanMaxDocs = 50

// KB refusals beyond the shared sentinels.
var (
	ErrKbQuota       = errors.New("tutor: kb document quota exceeded")
	ErrKbEmpty       = errors.New("tutor: kb content is empty")
	ErrKbTooLarge    = errors.New("tutor: kb input exceeds its cap")
	ErrKbParsing     = errors.New("tutor: this kb document is already parsing")
	ErrKbNoOriginal  = errors.New("tutor: this kb document has no original")
	ErrKbNoDocs      = errors.New("tutor: no usable kb documents")
	ErrKbExportEmpty = errors.New("tutor: kb export is empty")
	ErrBadKind       = errors.New("tutor: unknown material kind")
)

// KbMeta carries parse provenance and flow markers.
type KbMeta struct {
	SourceType string `json:"source_type,omitempty"`
	Parser     string `json:"parser,omitempty"`
	Pages      int    `json:"pages,omitempty"`
	Truncated  bool   `json:"truncated,omitempty"`
	Deduped    bool   `json:"deduped,omitempty"`
	AutoSunk   bool   `json:"auto_sunk,omitempty"`
}

// KbDoc is one library document: pending (staged upload awaiting confirm),
// confirmed (kept, not parsed), or parsed (text extracted). HasOriginal is
// false for text/url documents created straight to parsed.
type KbDoc struct {
	ID          string       `json:"id"`
	Title       string       `json:"title"`
	Kind        MaterialKind `json:"kind"`
	Ext         string       `json:"ext,omitempty"`
	Source      string       `json:"source"`
	HasOriginal bool         `json:"has_original"`
	Chars       int          `json:"parsed_chars,omitempty"`
	Status      string       `json:"status"`
	ParseMode   string       `json:"parse_mode,omitempty"`
	Meta        KbMeta       `json:"meta"`
	ContentHash string       `json:"content_hash,omitempty"`
	Error       string       `json:"error,omitempty"`
	CreatedAt   time.Time    `json:"created_at"`
	UpdatedAt   time.Time    `json:"updated_at"`
}

// Store rows live under kb/documents, originals and parsed beside them.

func (s *Store) kbDir() string      { return filepath.Join(s.root, "kb") }
func (s *Store) kbDocsDir() string  { return filepath.Join(s.kbDir(), "documents") }
func (s *Store) kbChatsDir() string { return filepath.Join(s.kbDir(), "chats") }

// KbDocs lists the library newest first — Deep-tutor's id-desc ordering.
func (s *Store) KbDocs() ([]KbDoc, error) {
	entries, err := os.ReadDir(s.kbDocsDir())
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var out []KbDoc
	for _, e := range entries {
		if !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		var d KbDoc
		if err := s.readJSON(filepath.Join(s.kbDocsDir(), e.Name()), &d); err == nil {
			out = append(out, d)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if !out[i].CreatedAt.Equal(out[j].CreatedAt) {
			return out[i].CreatedAt.After(out[j].CreatedAt)
		}
		return out[i].ID > out[j].ID
	})
	return out, nil
}

func (s *Store) KbDoc(id string) (*KbDoc, error) {
	var d KbDoc
	if err := s.readJSON(filepath.Join(s.kbDocsDir(), id+".json"), &d); err != nil {
		return nil, err
	}
	return &d, nil
}

// SaveKbDoc mints the id and timestamps on first write.
func (s *Store) SaveKbDoc(d *KbDoc) error {
	now := time.Now()
	if d.ID == "" {
		d.ID = newID()
		d.CreatedAt = now
	}
	d.UpdatedAt = now
	return s.writeJSON(filepath.Join(s.kbDocsDir(), d.ID+".json"), d)
}

func (s *Store) DeleteKbDoc(id string) error {
	if err := os.Remove(filepath.Join(s.kbDocsDir(), id+".json")); err != nil {
		if os.IsNotExist(err) {
			return ErrNotFound
		}
		return err
	}
	originals, _ := filepath.Glob(filepath.Join(s.kbDir(), "originals", id+".*"))
	for _, p := range originals {
		_ = os.Remove(p)
	}
	_ = os.Remove(filepath.Join(s.kbDir(), "parsed", id+".txt"))
	return nil
}

func (s *Store) WriteKbOriginal(id, ext string, data []byte) (string, error) {
	ext = strings.TrimPrefix(strings.ToLower(strings.TrimSpace(ext)), ".")
	if !safeExt.MatchString(ext) {
		ext = "bin"
	}
	p := filepath.Join(s.kbDir(), "originals", id+"."+ext)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return "", err
	}
	if err := s.writeRaw(p, data); err != nil {
		return "", err
	}
	return p, nil
}

// kbOriginalPath resolves a doc's original file by its recorded extension.
func (s *Store) kbOriginalPath(d *KbDoc) string {
	ext := strings.ToLower(d.Ext)
	if ext == "" || !safeExt.MatchString(ext) {
		ext = "bin"
	}
	return filepath.Join(s.kbDir(), "originals", d.ID+"."+ext)
}

func (s *Store) ReadKbOriginal(d *KbDoc) ([]byte, error) {
	data, err := os.ReadFile(s.kbOriginalPath(d))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return data, nil
}

func (s *Store) WriteKbParsed(id, text string) error {
	return s.writeRaw(filepath.Join(s.kbDir(), "parsed", id+".txt"), []byte(text))
}

func (s *Store) kbParsedPath(id string) string {
	return filepath.Join(s.kbDir(), "parsed", id+".txt")
}

func (s *Store) ReadKbParsed(id string) (string, error) {
	data, err := os.ReadFile(s.kbParsedPath(id))
	if err != nil {
		if os.IsNotExist(err) {
			return "", ErrNotFound
		}
		return "", err
	}
	return string(data), nil
}

// kbDedup finds a same-hash row, any status — re-uploads reuse it instead of
// spending quota.
func (s *Store) kbDedup(hash string) (*KbDoc, error) {
	docs, err := s.KbDocs()
	if err != nil {
		return nil, err
	}
	for i := range docs {
		if docs[i].ContentHash == hash {
			return &docs[i], nil
		}
	}
	return nil, nil
}

func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// Creation.

var (
	kbImageExts = map[string]bool{"png": true, "jpg": true, "jpeg": true, "gif": true,
		"bmp": true, "webp": true, "tiff": true, "tif": true}
	kbAudioExts = map[string]bool{"mp3": true, "wav": true, "m4a": true, "ogg": true, "flac": true}
	kbVideoExts = map[string]bool{"mp4": true, "mkv": true, "mov": true, "webm": true, "avi": true}
)

func kbKindForExt(ext string) MaterialKind {
	ext = strings.TrimPrefix(strings.ToLower(ext), ".")
	switch {
	case kbImageExts[ext]:
		return MaterialImage
	case kbAudioExts[ext]:
		return MaterialAudio
	case kbVideoExts[ext]:
		return MaterialVideo
	default:
		return MaterialFile
	}
}

// NormalizeMaterialKind validates a wire kind string.
func NormalizeMaterialKind(kind string) (MaterialKind, error) {
	switch MaterialKind(kind) {
	case MaterialFile, MaterialText, MaterialURL, MaterialImage, MaterialAudio, MaterialVideo:
		return MaterialKind(kind), nil
	default:
		return "", fmt.Errorf("%w: %s", ErrBadKind, kind)
	}
}

// dedupedCopy answers a dedup hit with the marker the frontend reads.
func dedupedCopy(d *KbDoc) *KbDoc {
	out := *d
	out.Meta.Deduped = true
	return &out
}

func (s *Service) kbQuotaCheck() error {
	docs, err := s.store.KbDocs()
	if err != nil {
		return err
	}
	if len(docs) >= KbMaxDocs {
		return fmt.Errorf("%w: at most %d documents", ErrKbQuota, KbMaxDocs)
	}
	return nil
}

// KbUpload stages a file: original bytes kept, row pending — parsing is the
// user's later decision, exactly Deep-tutor's iter-225 flow.
func (s *Service) KbUpload(filename string, data []byte) (*KbDoc, error) {
	if len(data) == 0 {
		return nil, ErrKbEmpty
	}
	if len(data) > maxMaterialBytes {
		return nil, fmt.Errorf("%w: document exceeds %d MB", ErrKbTooLarge, maxMaterialBytes>>20)
	}
	filename = path.Base(strings.ReplaceAll(filename, "\\", "/"))
	if filename == "" || filename == "." || filename == "/" {
		filename = "document"
	}
	ext := strings.TrimPrefix(path.Ext(filename), ".")
	hash := sha256Hex(data)
	if existing, err := s.store.kbDedup(hash); err != nil {
		return nil, err
	} else if existing != nil {
		return dedupedCopy(existing), nil
	}
	if err := s.kbQuotaCheck(); err != nil {
		return nil, err
	}
	doc := &KbDoc{
		ID: newID(), Title: clampRunes(filename, 256), Kind: kbKindForExt(ext),
		Ext: strings.ToLower(ext), Source: filename, HasOriginal: true,
		Status: "pending", ContentHash: hash, CreatedAt: s.opt.now(),
	}
	originalPath, err := s.store.WriteKbOriginal(doc.ID, ext, data)
	if err != nil {
		return nil, err
	}
	if err := s.store.SaveKbDoc(doc); err != nil {
		_ = os.Remove(originalPath) // orphan guard
		return nil, err
	}
	return doc, nil
}

// persistParsedDoc writes the parsed text then the row; a row-write failure
// removes the fresh file so no orphan survives.
func (s *Service) persistParsedDoc(doc *KbDoc, parsed *ParsedMaterial) (*KbDoc, error) {
	if err := s.store.WriteKbParsed(doc.ID, parsed.Text); err != nil {
		return nil, err
	}
	doc.Chars = len([]rune(parsed.Text))
	doc.Status = "parsed"
	doc.Error = ""
	doc.Meta.SourceType = parsed.Meta.SourceType
	doc.Meta.Parser = parsed.Meta.Parser
	doc.Meta.Pages = parsed.Meta.Pages
	doc.Meta.Truncated = strings.HasSuffix(parsed.Text, "…（超出长度上限已截断）")
	if err := s.store.SaveKbDoc(doc); err != nil {
		_ = os.Remove(s.store.kbParsedPath(doc.ID))
		return nil, err
	}
	return doc, nil
}

// KbAddText files pasted text: no original, parsed immediately through the
// same 500k truncation guard as materials.
func (s *Service) KbAddText(title, text string) (*KbDoc, error) {
	content := strings.TrimSpace(text)
	if content == "" {
		return nil, ErrKbEmpty
	}
	if len(text) > maxMaterialBytes {
		return nil, fmt.Errorf("%w: text exceeds %d MB", ErrKbTooLarge, maxMaterialBytes>>20)
	}
	hash := sha256Hex([]byte(text))
	if existing, err := s.store.kbDedup(hash); err != nil {
		return nil, err
	} else if existing != nil {
		return dedupedCopy(existing), nil
	}
	if err := s.kbQuotaCheck(); err != nil {
		return nil, err
	}
	parsed, err := ParseMaterial(context.Background(), MaterialInput{Kind: MaterialText, Text: text})
	if err != nil {
		return nil, err
	}
	if title = strings.TrimSpace(title); title == "" {
		title = clampRunes(strings.SplitN(content, "\n", 2)[0], 40)
	}
	doc := &KbDoc{
		ID: newID(), Title: clampRunes(title, 256), Kind: MaterialText, Ext: "txt",
		Source: clampRunes(content, 200), HasOriginal: false,
		Status: "parsed", ContentHash: hash, CreatedAt: s.opt.now(),
	}
	return s.persistParsedDoc(doc, parsed)
}

// KbAddURL files a fetched page: dedup keys on the canonical URL itself.
func (s *Service) KbAddURL(ctx context.Context, rawURL, title string) (*KbDoc, error) {
	normalized, err := parseMaterialURL(rawURL)
	if err != nil {
		return nil, err
	}
	hash := sha256Hex([]byte(normalized))
	if existing, err := s.store.kbDedup(hash); err != nil {
		return nil, err
	} else if existing != nil {
		return dedupedCopy(existing), nil
	}
	if err := s.kbQuotaCheck(); err != nil {
		return nil, err
	}
	parsed, err := ParseMaterial(ctx, MaterialInput{Kind: MaterialURL, Source: normalized})
	if err != nil {
		return nil, err
	}
	if title = strings.TrimSpace(title); title == "" {
		title = clampRunes(normalized, 80)
	}
	doc := &KbDoc{
		ID: newID(), Title: clampRunes(title, 256), Kind: MaterialURL,
		Source: clampRunes(normalized, 1024), HasOriginal: false,
		Status: "parsed", ContentHash: hash, CreatedAt: s.opt.now(),
	}
	return s.persistParsedDoc(doc, parsed)
}

// Lifecycle.

// KbConfirm moves a staged upload to confirmed; idempotent.
func (s *Service) KbConfirm(id string) (*KbDoc, error) {
	doc, err := s.store.KbDoc(id)
	if err != nil {
		return nil, err
	}
	if doc.Status == "pending" {
		doc.Status = "confirmed"
		if err := s.store.SaveKbDoc(doc); err != nil {
			return nil, err
		}
	}
	return doc, nil
}

// kbParseLock claims the per-document parse slot; false means one is running.
func (s *Service) kbParseLock(id string) bool {
	s.kbMu.Lock()
	defer s.kbMu.Unlock()
	if s.kbParsing == nil {
		s.kbParsing = map[string]bool{}
	}
	if s.kbParsing[id] {
		return false
	}
	s.kbParsing[id] = true
	return true
}

func (s *Service) kbParseUnlock(id string) {
	s.kbMu.Lock()
	delete(s.kbParsing, id)
	s.kbMu.Unlock()
}

// KbParse extracts text on demand: pending/confirmed become parsed (pending
// implies confirmation), an already-parsed doc may re-parse to switch mode.
// Failure keeps the row at its prior status with the reason recorded. In the
// file store ids never get reused, so a delete racing the parse can at worst
// orphan a parsed file, which the delete's glob cleanup also covers.
func (s *Service) KbParse(ctx context.Context, id, mode string) (*KbDoc, error) {
	if mode == "" {
		mode = "full"
	}
	if mode != "full" && mode != "fast" {
		return nil, errors.New("tutor: parse mode must be full or fast")
	}
	doc, err := s.store.KbDoc(id)
	if err != nil {
		return nil, err
	}
	if !doc.HasOriginal {
		return nil, ErrKbNoOriginal
	}
	if !s.kbParseLock(id) {
		return nil, ErrKbParsing
	}
	defer s.kbParseUnlock(id)

	data, err := s.store.ReadKbOriginal(doc)
	if err != nil {
		return nil, err
	}
	source := "document." + doc.Ext
	parsed, err := ParseMaterial(ctx, MaterialInput{Kind: doc.Kind, Source: source, Data: data})
	if err != nil {
		doc.Error = err.Error()
		_ = s.store.SaveKbDoc(doc)
		return doc, err
	}
	doc.ParseMode = mode
	doc, err = s.persistParsedDoc(doc, parsed)
	if err != nil {
		return nil, err
	}
	return doc, nil
}

// KbRename retitles a document; the old title never carries a file with it.
func (s *Service) KbRename(id, title string) (*KbDoc, error) {
	title = strings.TrimSpace(title)
	if title == "" {
		return nil, errors.New("tutor: title cannot be empty")
	}
	doc, err := s.store.KbDoc(id)
	if err != nil {
		return nil, err
	}
	doc.Title = clampRunes(title, 256)
	if err := s.store.SaveKbDoc(doc); err != nil {
		return nil, err
	}
	return doc, nil
}

// KbDelete removes row and files; a doc mid-parse refuses until it settles.
func (s *Service) KbDelete(id string) error {
	if _, err := s.store.KbDoc(id); err != nil {
		return err
	}
	if !s.kbParseLock(id) {
		return ErrKbParsing
	}
	defer s.kbParseUnlock(id)
	return s.store.DeleteKbDoc(id)
}

// KbRaw serves the original bytes (inline preview or download).
func (s *Service) KbRaw(id string) (*KbDoc, []byte, error) {
	doc, err := s.store.KbDoc(id)
	if err != nil {
		return nil, nil, err
	}
	if !doc.HasOriginal {
		return nil, nil, fmt.Errorf("%w: document %s has no original", ErrNotFound, id)
	}
	data, err := s.store.ReadKbOriginal(doc)
	if err != nil {
		return nil, nil, fmt.Errorf("%w: document %s original is missing", ErrNotFound, id)
	}
	return doc, data, nil
}

// KbParsedText answers the extracted text with its truncation flag.
func (s *Service) KbParsedText(id string) (map[string]any, error) {
	doc, err := s.store.KbDoc(id)
	if err != nil {
		return nil, err
	}
	if doc.Status != "parsed" {
		return nil, fmt.Errorf("%w: document %s has no parsed text", ErrNotFound, id)
	}
	text, err := s.store.ReadKbParsed(id)
	if err != nil {
		return nil, fmt.Errorf("%w: document %s parsed text is missing", ErrNotFound, id)
	}
	return map[string]any{
		"document_id": doc.ID, "title": doc.Title, "text": text,
		"parsed_chars": doc.Chars, "truncated": doc.Meta.Truncated,
	}, nil
}

// Listing and content search.

// KbDocList is the library view: newest first, optional kind filter, q
// matching titles (whole library) then parsed content (newest-50 scan),
// title hits ahead of content hits.
func (s *Service) KbDocList(q, kind string, limit int) ([]KbDoc, error) {
	if kind != "" {
		if _, err := NormalizeMaterialKind(kind); err != nil {
			return nil, err
		}
	}
	if limit <= 0 {
		limit = 200
	}
	if limit > 1000 {
		limit = 1000
	}
	docs, err := s.store.KbDocs()
	if err != nil {
		return nil, err
	}
	q = strings.TrimSpace(q)
	if kind != "" {
		kept := docs[:0]
		for i := range docs {
			if string(docs[i].Kind) == kind {
				kept = append(kept, docs[i])
			}
		}
		docs = kept
	}
	if q == "" {
		if len(docs) > limit {
			docs = docs[:limit]
		}
		return docs, nil
	}
	var out []KbDoc
	seen := map[string]bool{}
	for i := range docs {
		d := &docs[i]
		if kind != "" && string(d.Kind) != kind {
			continue
		}
		if !strings.Contains(strings.ToLower(d.Title), strings.ToLower(q)) {
			continue
		}
		seen[d.ID] = true
		out = append(out, *d)
		if len(out) >= limit {
			return out, nil
		}
	}
	contentDocs, _ := s.store.KbContentSearch(q, limit-len(out), seen, kind)
	out = append(out, contentDocs...)
	return out, nil
}

// kbHitSnippet centers the first case-insensitive hit with ±60 runes,
// whitespace folded to one line — Deep-tutor's _hit_snippet.
func kbHitSnippet(text, q string) string {
	runes := []rune(text)
	qRunes := []rune(strings.ToLower(q))
	folded := make([]rune, len(runes))
	for i, r := range runes {
		folded[i] = unicode.ToLower(r)
	}
	at := -1
	for i := 0; i+len(qRunes) <= len(folded); i++ {
		match := true
		for j := range qRunes {
			if folded[i+j] != qRunes[j] {
				match = false
				break
			}
		}
		if match {
			at = i
			break
		}
	}
	if at < 0 {
		return ""
	}
	start, end := at-60, at+len(qRunes)+60
	if start < 0 {
		start = 0
	}
	if end > len(runes) {
		end = len(runes)
	}
	body := strings.Join(strings.Fields(string(runes[start:end])), " ")
	if body == "" {
		return ""
	}
	if start > 0 {
		body = "…" + body
	}
	if end < len(runes) {
		body += "…"
	}
	return body
}

// KbContentSearch scans the newest parsedScanMaxDocs parsed documents for a
// content hit, returning docs paired with their snippets.
func (s *Store) KbContentSearch(q string, limit int, exclude map[string]bool, kind string) ([]KbDoc, []string) {
	if limit <= 0 || strings.TrimSpace(q) == "" {
		return nil, nil
	}
	docs, err := s.KbDocs()
	if err != nil {
		return nil, nil
	}
	scanned := 0
	var hitDocs []KbDoc
	var snippets []string
	for i := range docs {
		d := &docs[i]
		if d.Status != "parsed" {
			continue
		}
		if kind != "" && string(d.Kind) != kind {
			continue
		}
		scanned++
		if scanned > parsedScanMaxDocs {
			break
		}
		if exclude[d.ID] {
			continue
		}
		text, err := s.ReadKbParsed(d.ID)
		if err != nil || text == "" {
			continue
		}
		snippet := kbHitSnippet(text, q)
		if snippet == "" {
			continue
		}
		hitDocs = append(hitDocs, *d)
		snippets = append(snippets, snippet)
		if len(hitDocs) >= limit {
			break
		}
	}
	return hitDocs, snippets
}

// parseMaterialURL validates an http(s) link, materials' own guard.
func parseMaterialURL(rawURL string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || u.Scheme != "http" && u.Scheme != "https" || u.Host == "" {
		return "", errors.New("tutor: material url must be http(s)")
	}
	return u.String(), nil
}
