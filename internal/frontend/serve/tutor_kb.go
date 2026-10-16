// tutor_kb.go — the knowledge base over HTTP: documents through the
// upload/confirm/parse flow (text and URL straight to parsed), originals and
// parsed text reads, ZIP export, one-click generation from library documents,
// and the citation-backed KB chat with its persisted records.
package serve

import (
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	"reasonix/internal/runtime/tutor"
)

func (s *Server) registerTutorKbRoutes(mux *http.ServeMux) {
	mux.HandleFunc("POST /tutor/kb/documents/upload", s.tutorKbUpload)
	mux.HandleFunc("POST /tutor/kb/documents/text", s.tutorKbDocText)
	mux.HandleFunc("POST /tutor/kb/documents/url", s.tutorKbDocURL)
	mux.HandleFunc("GET /tutor/kb/documents", s.tutorKbDocs)
	mux.HandleFunc("GET /tutor/kb/documents/{id}", s.tutorKbDoc)
	mux.HandleFunc("PUT /tutor/kb/documents/{id}", s.tutorKbDocRename)
	mux.HandleFunc("DELETE /tutor/kb/documents/{id}", s.tutorKbDocDelete)
	mux.HandleFunc("POST /tutor/kb/documents/{id}/confirm", s.tutorKbDocConfirm)
	mux.HandleFunc("POST /tutor/kb/documents/{id}/parse", s.tutorKbDocParse)
	mux.HandleFunc("GET /tutor/kb/documents/{id}/raw", s.tutorKbDocRaw)
	mux.HandleFunc("GET /tutor/kb/documents/{id}/parsed", s.tutorKbDocParsed)

	mux.HandleFunc("GET /tutor/kb/export", s.tutorKbExport)
	mux.HandleFunc("POST /tutor/kb/generate", s.tutorKbGenerate)

	mux.HandleFunc("POST /tutor/kb/chat", s.tutorKbChat)
	mux.HandleFunc("GET /tutor/kb/chats", s.tutorKbChats)
	mux.HandleFunc("DELETE /tutor/kb/chats/{id}", s.tutorKbChatDelete)
}

// tutorKbUpload takes {filename, content_base64} — the kernel's base64-JSON
// upload shape, same as session materials.
func (s *Server) tutorKbUpload(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Filename      string `json:"filename"`
		ContentBase64 string `json:"content_base64"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 70<<20)).Decode(&body); err != nil ||
		strings.TrimSpace(body.ContentBase64) == "" {
		missingField(w, "content_base64")
		return
	}
	data, err := base64.StdEncoding.DecodeString(body.ContentBase64)
	if err != nil {
		refuse(w, http.StatusBadRequest, "tutor.bad_upload", "content_base64 did not decode", nil)
		return
	}
	doc, err := svc.KbUpload(body.Filename, data)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, doc)
}

func (s *Server) tutorKbDocText(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Title string `json:"title"`
		Text  string `json:"text"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 10<<20)).Decode(&body); err != nil ||
		strings.TrimSpace(body.Text) == "" {
		missingField(w, "text")
		return
	}
	doc, err := svc.KbAddText(body.Title, body.Text)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, doc)
}

func (s *Server) tutorKbDocURL(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		URL   string `json:"url"`
		Title string `json:"title"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.URL) == "" {
		missingField(w, "url")
		return
	}
	doc, err := svc.KbAddURL(r.Context(), body.URL, body.Title)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, doc)
}

func (s *Server) tutorKbDocs(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	docs, err := svc.KbDocList(r.URL.Query().Get("q"), r.URL.Query().Get("kind"), limit)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	if docs == nil {
		docs = []tutor.KbDoc{}
	}
	writeJSON(w, map[string]any{"documents": docs})
}

func (s *Server) tutorKbDoc(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	doc, err := svc.Store().KbDoc(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, doc)
}

func (s *Server) tutorKbDocRename(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Title string `json:"title"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil ||
		strings.TrimSpace(body.Title) == "" || len([]rune(strings.TrimSpace(body.Title))) > 256 {
		badValue(w, "title", "1-256 characters")
		return
	}
	doc, err := svc.KbRename(r.PathValue("id"), body.Title)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, doc)
}

func (s *Server) tutorKbDocDelete(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.KbDelete(r.PathValue("id")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) tutorKbDocConfirm(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	doc, err := svc.KbConfirm(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, doc)
}

func (s *Server) tutorKbDocParse(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Mode string `json:"mode"`
	}
	if r.Body != nil {
		defer r.Body.Close()
		_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body)
	}
	doc, err := svc.KbParse(r.Context(), r.PathValue("id"), body.Mode)
	if err != nil && doc == nil {
		tutorRefuse(w, err)
		return
	}
	if err != nil {
		// A parse failure keeps the row; the refusal carries the reason the
		// row also records, so the client can offer a mode switch.
		refuse(w, http.StatusUnprocessableEntity, "tutor.kb_parse_failed", doc.Error, nil)
		return
	}
	writeJSON(w, doc)
}

// kbRawMediaTypes maps originals a browser can render inline; anything else
// downloads as a generic binary.
var kbRawMediaTypes = map[string]string{
	"pdf": "application/pdf", "txt": "text/plain", "md": "text/markdown",
	"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
	"gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
	"tiff": "image/tiff", "tif": "image/tiff",
	"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
	"ogg": "audio/ogg", "flac": "audio/flac",
	"mp4": "video/mp4", "mkv": "video/x-matroska", "mov": "video/quicktime",
	"webm": "video/webm", "avi": "video/x-msvideo",
}

// tutorKbDocRaw serves the original: inline for the viewer by default,
// attachment with ?download=1, extension re-appended when a rename left the
// title without one.
func (s *Server) tutorKbDocRaw(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	doc, data, err := svc.KbRaw(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	mediaType := "application/octet-stream"
	if t, ok := kbRawMediaTypes[doc.Ext]; ok {
		mediaType = t
	}
	filename := doc.Title
	if filename == "" {
		filename = "kb-" + doc.ID
	}
	disposition := "inline"
	if r.URL.Query().Get("download") == "1" {
		disposition = "attachment"
		if doc.Ext != "" && !strings.HasSuffix(strings.ToLower(filename), "."+strings.ToLower(doc.Ext)) {
			filename += "." + doc.Ext
		}
	}
	w.Header().Set("Content-Type", mediaType)
	w.Header().Set("Content-Disposition",
		disposition+`; filename="kb-`+doc.ID+`"; filename*=UTF-8''`+url.PathEscape(filename))
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	_, _ = w.Write(data)
}

func (s *Server) tutorKbDocParsed(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	out, err := svc.KbParsedText(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, out)
}

// tutorKbExport streams the library (or the ?ids= selection) as a ZIP; the
// spooled temp file is removed once the copy lands.
func (s *Server) tutorKbExport(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var ids []string
	if raw := strings.TrimSpace(r.URL.Query().Get("ids")); raw != "" {
		for part := range strings.SplitSeq(raw, ",") {
			if part = strings.TrimSpace(part); part != "" {
				ids = append(ids, part)
			}
		}
	}
	path, count, err := svc.KbExportZip(ids)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	defer os.Remove(path)
	file, err := os.Open(path)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	defer file.Close()
	stamp := time.Now().Format("20060102-150405")
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition",
		`attachment; filename="kb-export-`+stamp+`.zip"; filename*=UTF-8''`+
			url.PathEscape("知识库导出-"+stamp+".zip"))
	w.Header().Set("X-Tutor-Kb-Docs", strconv.Itoa(count))
	if info, err := file.Stat(); err == nil {
		w.Header().Set("Content-Length", strconv.FormatInt(info.Size(), 10))
	}
	_, _ = io.Copy(w, file)
}

// tutorKbGenerate seeds a new generation session with the documents' parsed
// text; the frontend opens it as the ordinary generate flow.
func (s *Server) tutorKbGenerate(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		DocumentIDs []string `json:"document_ids"`
		Title       string   `json:"title"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || len(body.DocumentIDs) == 0 {
		missingField(w, "document_ids")
		return
	}
	if len(body.DocumentIDs) > 20 {
		badValue(w, "document_ids", "at most 20 documents")
		return
	}
	sid, count, err := svc.KbGenerateFromDocs(body.DocumentIDs, body.Title)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, map[string]any{"session_id": sid, "material_count": count})
}

// tutorKbChat answers one question from the library with citations.
func (s *Server) tutorKbChat(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Question    string   `json:"question"`
		DocumentIDs []string `json:"document_ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil ||
		strings.TrimSpace(body.Question) == "" {
		missingField(w, "question")
		return
	}
	result, err := svc.KbChat(r.Context(), body.Question, body.DocumentIDs)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, result)
}

func (s *Server) tutorKbChats(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
	out, err := svc.KbChatList(r.URL.Query().Get("q"), limit, offset)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, out)
}

func (s *Server) tutorKbChatDelete(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.KbChatDelete(r.PathValue("id")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}
