package tutor

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"
)

// The whole-store backup: one self-contained JSON document holding every
// learning asset, restorable by append as fresh rows. Deep Tutor exported its
// user the same way (iter 93); the Reasonix shape follows the tutor store,
// and credentials never enter it — model config lives in Reasonix's own
// config, outside this surface.
const (
	backupFormat   = "reasonix-tutor-backup"
	backupVersion  = 1
	backupMaxBytes = 256 << 20
)

// Restore-side quotas mirror Deep Tutor's iter-246 caps and this store's live
// limits: an import must not amplify resource use past what the app allows.
const (
	backupMaxSessions            = 200
	backupMaxMaterialsPerSession = 20
	backupMaxPlansPerSession     = 50
	backupMaxNodesPerPlan        = 100
	backupMaxQuizzesPerPlan      = 200
	backupMaxAttemptsPerQuiz     = 100
	backupMaxLogsPerPlan         = 2000
	backupMaxUnitsPerPlan        = 100
	backupMaxProgressPerPlan     = 500
)

var (
	ErrBadBackup      = errors.New("tutor: not a valid backup document")
	ErrBackupTooLarge = errors.New("tutor: backup exceeds the size cap")
	ErrImportFailed   = errors.New("tutor: import failed and was rolled back")
)

type BackupDoc struct {
	Format     string          `json:"format"`
	Version    int             `json:"version"`
	ExportedAt string          `json:"exported_at"`
	Sessions   []BackupSession `json:"sessions"`
	Notes      []BackupNote    `json:"notes"`
	Kb         []BackupKbDoc   `json:"kb"`
}

type BackupSession struct {
	Title     string           `json:"title"`
	CreatedAt string           `json:"created_at,omitempty"`
	Materials []BackupMaterial `json:"materials,omitempty"`
	Plans     []BackupPlan     `json:"plans,omitempty"`
}

type BackupMaterial struct {
	Kind       string            `json:"kind"`
	Source     string            `json:"source,omitempty"`
	ParseMode  string            `json:"parse_mode,omitempty"`
	Status     string            `json:"status,omitempty"`
	Chars      int               `json:"chars,omitempty"`
	Meta       map[string]string `json:"meta,omitempty"`
	CreatedAt  string            `json:"created_at,omitempty"`
	ParsedText string            `json:"parsed_text,omitempty"`
}

type BackupPlan struct {
	Version    int               `json:"version"`
	Title      string            `json:"title,omitempty"`
	Topic      string            `json:"topic,omitempty"`
	CreatedAt  string            `json:"created_at,omitempty"`
	Intent     Intent            `json:"intent"`
	Knowledge  KnowledgeMap      `json:"knowledge_map"`
	Stages     []Stage           `json:"stages"`
	Schedule   []ScheduleDay     `json:"schedule,omitempty"`
	Validation *ValidationReport `json:"validation_report,omitempty"`
	Quizzes    []BackupQuiz      `json:"quizzes,omitempty"`
	Attempts   []QuizAttempt     `json:"attempts,omitempty"`
	Progress   []Progress        `json:"progress,omitempty"`
	Logs       []LearningLog     `json:"logs,omitempty"`
	Units      []StudyUnit       `json:"units,omitempty"`
}

type BackupQuiz struct {
	ID      string      `json:"id,omitempty"`
	NodeID  string      `json:"node_id,omitempty"`
	Type    QuizType    `json:"type"`
	Payload QuizPayload `json:"payload"`
}

type BackupNote struct {
	Title     string `json:"title"`
	Body      string `json:"body"`
	CreatedAt string `json:"created_at,omitempty"`
	UpdatedAt string `json:"updated_at,omitempty"`
}

type BackupKbDoc struct {
	Title       string       `json:"title"`
	Kind        MaterialKind `json:"kind"`
	Ext         string       `json:"ext,omitempty"`
	Source      string       `json:"source,omitempty"`
	Chars       int          `json:"chars,omitempty"`
	Meta        KbMeta       `json:"meta,omitempty"`
	ContentHash string       `json:"content_hash,omitempty"`
	CreatedAt   string       `json:"created_at,omitempty"`
	ParsedText  string       `json:"parsed_text"`
}

// BuildBackup assembles the whole store into doc. The parsed texts ride
// inline, so one document restores everything without the originals.
func (s *Service) BuildBackup() (*BackupDoc, error) {
	doc := &BackupDoc{Format: backupFormat, Version: backupVersion, ExportedAt: s.opt.now().UTC().Format(time.RFC3339)}
	sessions, err := s.store.Sessions()
	if err != nil {
		return nil, err
	}
	plans, err := s.store.Plans()
	if err != nil {
		return nil, err
	}
	bySession := map[string][]PlanSummary{}
	for _, p := range plans {
		bySession[p.SessionID] = append(bySession[p.SessionID], p)
	}
	for _, sess := range sessions {
		out := BackupSession{Title: sess.Title, CreatedAt: sess.CreatedAt.UTC().Format(time.RFC3339)}
		materials, err := s.store.Materials(sess.ID)
		if err != nil {
			return nil, err
		}
		for _, m := range materials {
			bm := BackupMaterial{
				Kind: string(m.Kind), Source: m.Source, ParseMode: m.ParseMode,
				Status: m.Status, Chars: m.Chars, Meta: m.Meta,
				CreatedAt: m.CreatedAt.UTC().Format(time.RFC3339),
			}
			if m.Status == "parsed" {
				if text, err := s.store.ReadMaterialParsed(sess.ID, m.ID); err == nil {
					bm.ParsedText = text
				}
			}
			out.Materials = append(out.Materials, bm)
		}
		for _, ps := range bySession[sess.ID] {
			bp, err := s.backupPlan(ps.ID)
			if err != nil {
				return nil, err
			}
			out.Plans = append(out.Plans, *bp)
		}
		doc.Sessions = append(doc.Sessions, out)
	}
	notes, err := s.store.Notes()
	if err != nil {
		return nil, err
	}
	for _, n := range notes {
		doc.Notes = append(doc.Notes, BackupNote{
			Title: n.Title, Body: n.Body,
			CreatedAt: n.CreatedAt.UTC().Format(time.RFC3339),
			UpdatedAt: n.UpdatedAt.UTC().Format(time.RFC3339),
		})
	}
	kbDocs, err := s.store.KbDocs()
	if err != nil {
		return nil, err
	}
	for _, d := range kbDocs {
		bd := BackupKbDoc{
			Title: d.Title, Kind: d.Kind, Ext: d.Ext, Source: d.Source,
			Chars: d.Chars, Meta: d.Meta, ContentHash: d.ContentHash,
			CreatedAt: d.CreatedAt.UTC().Format(time.RFC3339),
		}
		if text, err := s.store.ReadKbParsed(d.ID); err == nil {
			bd.ParsedText = text
		}
		doc.Kb = append(doc.Kb, bd)
	}
	return doc, nil
}

func (s *Service) backupPlan(planID string) (*BackupPlan, error) {
	plan, err := s.store.Plan(planID)
	if err != nil {
		return nil, err
	}
	out := &BackupPlan{
		Version: plan.Version, Title: plan.Title, Topic: plan.Topic,
		CreatedAt: plan.CreatedAt.UTC().Format(time.RFC3339),
		Intent:    plan.Intent, Knowledge: plan.Knowledge, Stages: plan.Stages,
		Schedule: plan.Schedule, Validation: plan.Validation,
	}
	quizzes, err := s.store.Quizzes(planID)
	if err != nil {
		return nil, err
	}
	for _, q := range quizzes {
		out.Quizzes = append(out.Quizzes, BackupQuiz{ID: q.ID, NodeID: q.NodeID, Type: q.Type, Payload: q.Payload})
	}
	if attempts, err := s.store.Attempts(planID); err != nil {
		return nil, err
	} else {
		out.Attempts = attempts
	}
	if progress, err := s.store.ProgressList(planID); err != nil {
		return nil, err
	} else {
		out.Progress = progress
	}
	if logs, err := s.store.Logs(planID); err != nil {
		return nil, err
	} else {
		out.Logs = logs
	}
	if units, err := s.store.StudyUnits(planID); err != nil {
		return nil, err
	} else {
		out.Units = units
	}
	return out, nil
}

// WriteBackup encodes doc to w as one compact JSON stream.
func WriteBackup(w io.Writer, doc *BackupDoc) error {
	enc := json.NewEncoder(w)
	return enc.Encode(doc)
}

// ParseBackup validates raw as a backup document within the size cap and
// decodes it. Structure problems answer ErrBadBackup.
func ParseBackup(raw []byte) (*BackupDoc, error) {
	if len(raw) > backupMaxBytes {
		return nil, fmt.Errorf("%w: over %d MB", ErrBackupTooLarge, backupMaxBytes>>20)
	}
	var doc BackupDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, ErrBadBackup
	}
	if doc.Format != backupFormat {
		return nil, ErrBadBackup
	}
	if doc.Version < 1 || doc.Version > backupVersion {
		return nil, fmt.Errorf("%w: version %d unsupported", ErrBadBackup, doc.Version)
	}
	return &doc, nil
}

// BackupPreview counts what an import would restore, changing nothing. The
// keys match the restore summary so one UI serves both.
func BackupPreview(doc *BackupDoc) map[string]any {
	preview := restoreSummary()
	for i := range doc.Sessions {
		sess := &doc.Sessions[i]
		preview["sessions"] = preview["sessions"].(int) + 1
		preview["materials"] = preview["materials"].(int) + len(sess.Materials)
		preview["plans"] = preview["plans"].(int) + len(sess.Plans)
		for j := range sess.Plans {
			p := &sess.Plans[j]
			preview["knowledge_nodes"] = preview["knowledge_nodes"].(int) + len(p.Knowledge.Nodes)
			preview["quizzes"] = preview["quizzes"].(int) + len(p.Quizzes)
			preview["attempts"] = preview["attempts"].(int) + len(p.Attempts)
			preview["progress"] = preview["progress"].(int) + len(p.Progress)
			preview["learning_logs"] = preview["learning_logs"].(int) + len(p.Logs)
			preview["study_units"] = preview["study_units"].(int) + len(p.Units)
		}
	}
	preview["notes"] = len(doc.Notes)
	preview["kb_documents"] = len(doc.Kb)
	preview["exported_at"] = doc.ExportedAt
	return preview
}

func restoreSummary() map[string]any {
	return map[string]any{
		"sessions": 0, "materials": 0, "plans": 0, "knowledge_nodes": 0,
		"quizzes": 0, "attempts": 0, "progress": 0, "learning_logs": 0,
		"study_units": 0, "notes": 0, "kb_documents": 0,
		"skipped": []string{},
	}
}

func parseBackupTime(v string) (time.Time, bool) {
	if v == "" {
		return time.Time{}, false
	}
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {
		if t, err := time.Parse(layout, v); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// RestoreBackup appends the document as new rows: fresh ids everywhere, no
// overwrites, quotas and clamps matching the live caps, over-quota items
// skipped and reported. A write failure rolls back everything this import
// created — the file store has no transactions, so the restore tracks every
// path it adds and removes them on error before answering ErrImportFailed.
func (s *Service) RestoreBackup(doc *BackupDoc) (map[string]any, error) {
	summary := restoreSummary()
	var skipped []string
	var created []string
	add := func(format string, args ...any) string {
		msg := fmt.Sprintf(format, args...)
		skipped = append(skipped, msg)
		return msg
	}
	fail := func(err error) (map[string]any, error) {
		for _, dir := range slices.Backward(created) {
			_ = os.RemoveAll(dir)
		}
		return nil, fmt.Errorf("%w: %w", ErrImportFailed, err)
	}
	keep := func(path string) {
		created = append(created, path)
	}

	existingSessions, err := s.store.Sessions()
	if err != nil {
		return nil, err
	}
	sessionRoom := backupMaxSessions - len(existingSessions)
	for si := range doc.Sessions {
		src := &doc.Sessions[si]
		if sessionRoom <= 0 {
			add("会话数达上限 %d，其余会话未导入", backupMaxSessions)
			break
		}
		sessionRoom--
		sess, err := s.store.CreateSession(clampRunes(src.Title, 256))
		if err != nil {
			return fail(err)
		}
		keep(s.store.sessionDir(sess.ID))
		if t, ok := parseBackupTime(src.CreatedAt); ok {
			sess.CreatedAt = t
			if err := s.store.SaveSession(sess); err != nil {
				return fail(err)
			}
		}
		summary["sessions"] = summary["sessions"].(int) + 1

		matsRoom := backupMaxMaterialsPerSession
		matsSkipped := 0
		for mi := range src.Materials {
			if matsRoom <= 0 {
				matsSkipped++
				continue
			}
			bm := &src.Materials[mi]
			kind, err := NormalizeMaterialKind(bm.Kind)
			if err != nil {
				add("资料 kind 非法（%q），已跳过", bm.Kind)
				continue
			}
			matsRoom--
			mat := &Material{
				ID: newID(), SessionID: sess.ID, Kind: kind,
				Source: clampRunes(bm.Source, 200), ParseMode: bm.ParseMode,
				Status: bm.Status, Meta: bm.Meta,
				CreatedAt: s.opt.now(),
			}
			if t, ok := parseBackupTime(bm.CreatedAt); ok {
				mat.CreatedAt = t
			}
			if bm.ParsedText != "" {
				mat.Status = "parsed"
				mat.Chars = len([]rune(bm.ParsedText))
			}
			if err := s.store.SaveMaterial(mat); err != nil {
				return fail(err)
			}
			if bm.ParsedText != "" {
				if err := s.store.WriteMaterialParsed(sess.ID, mat.ID, bm.ParsedText); err != nil {
					return fail(err)
				}
			}
			summary["materials"] = summary["materials"].(int) + 1
		}
		if matsSkipped > 0 {
			add("会话「%s」资料超每会话 %d 份上限，%d 份未导入", sess.Title, backupMaxMaterialsPerSession, matsSkipped)
		}

		plansRoom := backupMaxPlansPerSession
		plansSkipped := 0
		for pi := range src.Plans {
			if plansRoom <= 0 {
				plansSkipped++
				continue
			}
			plansRoom--
			bp := &src.Plans[pi]
			if err := s.restorePlan(sess, bp, summary, &skipped, keep); err != nil {
				return fail(err)
			}
		}
		if plansSkipped > 0 {
			add("会话「%s」方案超每会话 %d 个上限，%d 个未导入", sess.Title, backupMaxPlansPerSession, plansSkipped)
		}
	}

	if err := s.restoreNotes(doc.Notes, summary, &skipped); err != nil {
		return fail(err)
	}
	if err := s.restoreKb(doc.Kb, summary, &skipped, keep); err != nil {
		return fail(err)
	}
	summary["skipped"] = skipped
	return summary, nil
}

func (s *Service) restorePlan(sess *GenSession, bp *BackupPlan, summary map[string]any, skipped *[]string, keep func(string)) error {
	add := func(format string, args ...any) {
		*skipped = append(*skipped, fmt.Sprintf(format, args...))
	}
	if len(bp.Knowledge.Nodes) > backupMaxNodesPerPlan {
		bp.Knowledge.Nodes = bp.Knowledge.Nodes[:backupMaxNodesPerPlan]
		add("方案「%s」知识节点超 %d 上限，已截断", planLabel(bp), backupMaxNodesPerPlan)
	}
	planID := newID()
	plan := &Plan{
		ID: planID, SessionID: sess.ID, Version: max(bp.Version, 1),
		Title: clampRunes(bp.Title, 256), Topic: clampRunes(bp.Topic, 256),
		CreatedAt: s.opt.now(), UpdatedAt: s.opt.now(),
		Intent: bp.Intent, Knowledge: bp.Knowledge, Stages: bp.Stages,
		Schedule: bp.Schedule, Validation: bp.Validation,
	}
	if t, ok := parseBackupTime(bp.CreatedAt); ok {
		plan.CreatedAt = t
	}
	if err := s.store.SavePlan(plan); err != nil {
		return err
	}
	keep(s.store.planDir(planID))
	if sess.PlanID == "" {
		sess.PlanID = planID
		if err := s.store.SaveSession(sess); err != nil {
			return err
		}
	}
	summary["plans"] = summary["plans"].(int) + 1
	summary["knowledge_nodes"] = summary["knowledge_nodes"].(int) + len(bp.Knowledge.Nodes)

	if err := s.restoreQuizzes(planID, bp, summary, skipped); err != nil {
		return err
	}
	if err := s.restorePlanExtras(planID, bp, summary, skipped); err != nil {
		return err
	}
	return nil
}

// restoreQuizzes rebuilds the plan's quiz set with fresh ids and remaps the
// backup's attempts onto them, keeping the answer side of every payload.
func (s *Service) restoreQuizzes(planID string, bp *BackupPlan, summary map[string]any, skipped *[]string) error {
	add := func(format string, args ...any) {
		*skipped = append(*skipped, fmt.Sprintf(format, args...))
	}
	quizIDMap := map[string]string{}
	quizzes := make([]Quiz, 0, len(bp.Quizzes))
	for qi := range bp.Quizzes {
		if len(quizzes) >= backupMaxQuizzesPerPlan {
			add("方案「%s」测验超 %d 上限，其余未导入", planLabel(bp), backupMaxQuizzesPerPlan)
			break
		}
		bq := &bp.Quizzes[qi]
		id := newID()
		if bq.ID != "" {
			quizIDMap[bq.ID] = id
		}
		quizzes = append(quizzes, Quiz{
			ID: id, PlanID: planID, NodeID: bq.NodeID, Type: bq.Type,
			Payload:   bq.Payload,
			CreatedAt: s.opt.now(),
		})
	}
	if len(quizzes) > 0 {
		if err := s.store.SaveQuizzes(planID, quizzes); err != nil {
			return err
		}
	}
	summary["quizzes"] = summary["quizzes"].(int) + len(quizzes)

	if len(bp.Attempts) == 0 {
		return nil
	}
	validIDs := map[string]bool{}
	for _, q := range quizzes {
		validIDs[q.ID] = true
	}
	counts := map[string]int{}
	attemptsKept := 0
	for _, a := range bp.Attempts {
		// Attempts point at the backup's quiz ids; only remapped and
		// in-cap attempts land.
		id, mapped := quizIDMap[a.QuizID]
		if !mapped {
			if !validIDs[a.QuizID] {
				continue
			}
			id = a.QuizID
		}
		if counts[id] >= backupMaxAttemptsPerQuiz {
			continue
		}
		counts[id]++
		a.QuizID = id
		if err := s.store.AppendAttempt(planID, a); err != nil {
			return err
		}
		attemptsKept++
	}
	summary["attempts"] = summary["attempts"].(int) + attemptsKept
	return nil
}

// restorePlanExtras lands the progress rows, learning logs, and study units,
// each under its own cap with clamps matching the live write paths.
func (s *Service) restorePlanExtras(planID string, bp *BackupPlan, summary map[string]any, skipped *[]string) error {
	add := func(format string, args ...any) {
		*skipped = append(*skipped, fmt.Sprintf(format, args...))
	}
	if len(bp.Progress) > 0 {
		var progress []Progress
		for _, pr := range bp.Progress {
			if len(progress) >= backupMaxProgressPerPlan {
				break
			}
			pr.Mastery = min(max(pr.Mastery, 0), 1)
			progress = append(progress, pr)
		}
		if err := s.store.SaveProgress(planID, progress); err != nil {
			return err
		}
		summary["progress"] = summary["progress"].(int) + len(progress)
	}

	if len(bp.Logs) > 0 {
		var logs []LearningLog
		for _, lg := range bp.Logs {
			if len(logs) >= backupMaxLogsPerPlan {
				add("方案「%s」打卡记录超 %d 上限，其余未导入", planLabel(bp), backupMaxLogsPerPlan)
				break
			}
			lg.ID = newID()
			lg.PlanID = planID
			lg.Minutes = min(max(lg.Minutes, 1), 1440)
			logs = append(logs, lg)
		}
		if len(logs) > 0 {
			if err := s.store.writeJSON(filepath.Join(s.store.planDir(planID), "logs.json"), logs); err != nil {
				return err
			}
		}
		summary["learning_logs"] = summary["learning_logs"].(int) + len(logs)
	}

	if len(bp.Units) > 0 {
		var units []StudyUnit
		for _, u := range bp.Units {
			if len(units) >= backupMaxUnitsPerPlan {
				add("方案「%s」自学单元超 %d 上限，其余未导入", planLabel(bp), backupMaxUnitsPerPlan)
				break
			}
			u.ID = newID()
			units = append(units, u)
		}
		if len(units) > 0 {
			if err := s.store.SaveStudyUnits(planID, units); err != nil {
				return err
			}
		}
		summary["study_units"] = summary["study_units"].(int) + len(units)
	}
	return nil
}

func planLabel(bp *BackupPlan) string {
	if label := strings.TrimSpace(bp.Title); label != "" {
		return clampRunes(label, 64)
	}
	if label := strings.TrimSpace(bp.Topic); label != "" {
		return clampRunes(label, 64)
	}
	return "未命名方案"
}

func (s *Service) restoreNotes(notes []BackupNote, summary map[string]any, skipped *[]string) error {
	existing, err := s.store.Notes()
	if err != nil {
		return err
	}
	room := noteQuota - len(existing)
	skippedNotes := 0
	for i := range notes {
		n := &notes[i]
		title := clampRunes(strings.TrimSpace(n.Title), noteTitleMax)
		body := clampRunes(n.Body, noteBodyMax)
		if title == "" && strings.TrimSpace(body) == "" {
			continue
		}
		if room <= 0 {
			skippedNotes++
			continue
		}
		room--
		note := &Note{Title: title, Body: body}
		if t, ok := parseBackupTime(n.CreatedAt); ok {
			note.CreatedAt = t
		}
		if err := s.store.SaveNote(note); err != nil {
			return err
		}
		summary["notes"] = summary["notes"].(int) + 1
	}
	if skippedNotes > 0 {
		*skipped = append(*skipped, fmt.Sprintf("笔记数达上限 %d 条，%d 条未导入", noteQuota, skippedNotes))
	}
	return nil
}

func (s *Service) restoreKb(docs []BackupKbDoc, summary map[string]any, skipped *[]string, keep func(string)) error {
	existing, err := s.store.KbDocs()
	if err != nil {
		return err
	}
	room := KbMaxDocs - len(existing)
	skippedDocs := 0
	for i := range docs {
		bd := &docs[i]
		if strings.TrimSpace(bd.ParsedText) == "" {
			continue
		}
		kind, err := NormalizeMaterialKind(string(bd.Kind))
		if err != nil {
			*skipped = append(*skipped, fmt.Sprintf("知识库文档「%s」kind 非法，已跳过", clampRunes(bd.Title, 64)))
			continue
		}
		if bd.ContentHash != "" {
			if dup, err := s.store.kbDedup(bd.ContentHash); err != nil {
				return err
			} else if dup != nil {
				continue
			}
		}
		if room <= 0 {
			skippedDocs++
			continue
		}
		room--
		doc := &KbDoc{
			ID: newID(), Title: clampRunes(bd.Title, 256), Kind: kind,
			Ext: bd.Ext, Source: clampRunes(bd.Source, 200), HasOriginal: false,
			Chars: len([]rune(bd.ParsedText)), Status: "parsed",
			Meta: bd.Meta, ContentHash: bd.ContentHash, CreatedAt: s.opt.now(),
		}
		if t, ok := parseBackupTime(bd.CreatedAt); ok {
			doc.CreatedAt = t
		}
		if err := s.store.WriteKbParsed(doc.ID, bd.ParsedText); err != nil {
			return err
		}
		keep(s.store.kbParsedPath(doc.ID))
		if err := s.store.SaveKbDoc(doc); err != nil {
			return err
		}
		keep(filepath.Join(s.store.kbDocsDir(), doc.ID+".json"))
		summary["kb_documents"] = summary["kb_documents"].(int) + 1
	}
	if skippedDocs > 0 {
		*skipped = append(*skipped, fmt.Sprintf("知识库文档数达上限 %d 篇，%d 篇未导入", KbMaxDocs, skippedDocs))
	}
	return nil
}
