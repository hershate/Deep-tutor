package tutor

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"path"
	"strings"
)

// maxMaterialBytes bounds one uploaded original, Deep-tutor's upload cap.
const maxMaterialBytes = 50 << 20

// AddMaterialText stages pasted text: the content itself is the original, so
// the parse step needs no re-read.
func (s *Service) AddMaterialText(sessionID, name, content string) (*Material, error) {
	if strings.TrimSpace(content) == "" {
		return nil, errors.New("tutor: pasted material is empty")
	}
	if len(content) > maxMaterialBytes {
		return nil, fmt.Errorf("tutor: material exceeds %d MB cap", maxMaterialBytes>>20)
	}
	if _, err := s.store.Session(sessionID); err != nil {
		return nil, err
	}
	if name == "" {
		name = "粘贴文本"
	}
	m := &Material{
		ID: newID(), SessionID: sessionID, Kind: MaterialText, Source: name,
		Status: "pending", Chars: len([]rune(content)), CreatedAt: s.opt.now(),
	}
	if _, err := s.store.WriteMaterialOriginal(sessionID, m.ID, "txt", []byte(content)); err != nil {
		return nil, err
	}
	if err := s.store.SaveMaterial(m); err != nil {
		return nil, err
	}
	return m, nil
}

// AddMaterialURL stages a link for fetching at parse time.
func (s *Service) AddMaterialURL(sessionID, rawURL string) (*Material, error) {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || u.Scheme != "http" && u.Scheme != "https" || u.Host == "" {
		return nil, errors.New("tutor: material url must be http(s)")
	}
	if _, err := s.store.Session(sessionID); err != nil {
		return nil, err
	}
	m := &Material{
		ID: newID(), SessionID: sessionID, Kind: MaterialURL, Source: u.String(),
		Status: "pending", CreatedAt: s.opt.now(),
	}
	if err := s.store.SaveMaterial(m); err != nil {
		return nil, err
	}
	return m, nil
}

// AddMaterialFile stages an uploaded file, keeping the original bytes for the
// parse step.
func (s *Service) AddMaterialFile(sessionID, filename string, data []byte) (*Material, error) {
	if len(data) == 0 {
		return nil, errors.New("tutor: uploaded material is empty")
	}
	if len(data) > maxMaterialBytes {
		return nil, fmt.Errorf("tutor: material exceeds %d MB cap", maxMaterialBytes>>20)
	}
	if _, err := s.store.Session(sessionID); err != nil {
		return nil, err
	}
	filename = path.Base(strings.ReplaceAll(filename, "\\", "/"))
	if filename == "" || filename == "." || filename == "/" {
		filename = "material"
	}
	m := &Material{
		ID: newID(), SessionID: sessionID, Kind: MaterialFile, Source: filename,
		Status: "pending", CreatedAt: s.opt.now(),
	}
	ext := strings.TrimPrefix(path.Ext(filename), ".")
	if _, err := s.store.WriteMaterialOriginal(sessionID, m.ID, ext, data); err != nil {
		return nil, err
	}
	if err := s.store.SaveMaterial(m); err != nil {
		return nil, err
	}
	return m, nil
}

func (s *Service) Materials(sessionID string) ([]Material, error) {
	if _, err := s.store.Session(sessionID); err != nil {
		return nil, err
	}
	return s.store.Materials(sessionID)
}

func (s *Service) findMaterial(sessionID, materialID string) (*Material, error) {
	materials, err := s.store.Materials(sessionID)
	if err != nil {
		return nil, err
	}
	for i := range materials {
		if materials[i].ID == materialID {
			return &materials[i], nil
		}
	}
	return nil, ErrNotFound
}

// ConfirmMaterial moves a staged input to confirmed, recording the parse
// depth the person chose.
func (s *Service) ConfirmMaterial(sessionID, materialID, mode string) (*Material, error) {
	if mode == "" {
		mode = "full"
	}
	if mode != "full" && mode != "fast" && mode != "none" {
		return nil, errors.New("tutor: parse mode must be full, fast, or none")
	}
	m, err := s.findMaterial(sessionID, materialID)
	if err != nil {
		return nil, err
	}
	if m.Status != "pending" && m.Status != "confirmed" {
		return nil, errors.New("tutor: material already parsed")
	}
	m.Status = "confirmed"
	m.ParseMode = mode
	m.Error = ""
	if err := s.store.SaveMaterial(m); err != nil {
		return nil, err
	}
	return m, nil
}

// ParseMaterialFor parses one confirmed input and stores the text; a parse
// failure keeps the material confirmed with the reason on the row.
func (s *Service) ParseMaterialFor(ctx context.Context, sessionID, materialID string) (*Material, error) {
	m, err := s.findMaterial(sessionID, materialID)
	if err != nil {
		return nil, err
	}
	if m.ParseMode == "none" {
		return m, nil
	}
	if m.Status == "parsed" {
		return m, nil
	}
	if m.Status != "confirmed" {
		return nil, errors.New("tutor: material must be confirmed before parsing")
	}
	in := MaterialInput{Kind: m.Kind, Source: m.Source}
	switch m.Kind {
	case MaterialText:
		text, err := s.store.ReadMaterialOriginal(sessionID, materialID)
		if err != nil {
			return nil, err
		}
		in.Text = text
	case MaterialFile:
		data, err := s.store.ReadMaterialOriginalBytes(sessionID, materialID, m.Source)
		if err != nil {
			return nil, err
		}
		in.Data = data
	case MaterialURL:
		// fetched inside ParseMaterial
	default:
		return nil, fmt.Errorf("%w: %s", ErrUnsupportedFormat, m.Kind)
	}
	parsed, err := ParseMaterial(ctx, in)
	if err != nil {
		m.Error = err.Error()
		_ = s.store.SaveMaterial(m)
		return m, err
	}
	if err := s.store.WriteMaterialParsed(sessionID, materialID, parsed.Text); err != nil {
		return nil, err
	}
	m.Status = "parsed"
	m.Chars = len([]rune(parsed.Text))
	m.Error = ""
	if err := s.store.SaveMaterial(m); err != nil {
		return nil, err
	}
	return m, nil
}

func (s *Service) DeleteMaterial(sessionID, materialID string) error {
	if _, err := s.findMaterial(sessionID, materialID); err != nil {
		return err
	}
	return s.store.DeleteMaterial(sessionID, materialID)
}
