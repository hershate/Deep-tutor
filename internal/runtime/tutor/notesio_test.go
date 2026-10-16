package tutor

import (
	"archive/zip"
	"bytes"
	"errors"
	"strings"
	"testing"
	"time"
)

func zipNoteFixture(t *testing.T, entries map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, body := range entries {
		w, err := zw.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestExportNotesZipContent(t *testing.T) {
	store := openTestStore(t)
	now := time.Now()
	for _, n := range []Note{
		{ID: "n1", Title: "笔记一", Body: "笔记一内容\n第二行", UpdatedAt: now},
		{ID: "n2", Title: "笔记二", Body: "笔记二内容", UpdatedAt: now},
	} {
		if err := store.SaveNote(&n); err != nil {
			t.Fatal(err)
		}
	}
	notes, err := store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	blob, err := ExportNotesZip(notes, now)
	if err != nil {
		t.Fatal(err)
	}
	zr, err := zip.NewReader(bytes.NewReader(blob), int64(len(blob)))
	if err != nil {
		t.Fatalf("zip: %v", err)
	}
	var index, bodies string
	mdCount := 0
	for _, f := range zr.File {
		data, err := readZipEntry(zr, f.Name)
		if err != nil {
			t.Fatal(err)
		}
		if f.Name == "index.md" {
			index = string(data)
			continue
		}
		if strings.HasSuffix(f.Name, ".md") {
			mdCount++
			bodies += string(data)
			if !strings.HasPrefix(string(data), "# ") {
				t.Errorf("%s does not start with a heading", f.Name)
			}
		}
	}
	if mdCount != 2 {
		t.Fatalf("md files = %d", mdCount)
	}
	if !strings.Contains(bodies, "笔记一内容") || !strings.Contains(bodies, "笔记二内容") {
		t.Error("note bodies missing from export")
	}
	if !strings.Contains(index, "笔记数：2") {
		t.Errorf("index lacks count: %q", index)
	}
}

func TestExportNotesZipEmptyRefuses(t *testing.T) {
	store := openTestStore(t)
	if notes, _ := store.Notes(); len(notes) != 0 {
		t.Fatal("store not empty")
	}
	if _, err := ExportNotesZip(nil, time.Now()); !errors.Is(err, ErrNotFound) {
		t.Fatalf("err = %v", err)
	}
}

func TestImportNotesMdAndZip(t *testing.T) {
	store := openTestStore(t)
	zipBytes := zipNoteFixture(t, map[string]string{
		"x.md":       "# 压缩甲\n\n内容甲",
		"sub/y.md":   "# 压缩乙\n\n内容乙",
		"ignore.txt": "not markdown",
	})
	result, err := ImportNotes(store, []NoteImportFile{
		{Name: "a.md", Data: []byte("# 标题甲\n\n正文甲")},
		{Name: "b.md", Data: []byte("只有正文内容")},
		{Name: "notes.zip", Data: zipBytes},
		{Name: "bad.zip", Data: []byte("not a zip")},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result.Imported != 4 || result.Skipped != 1 {
		t.Fatalf("result = %+v (imported 4, skipped 1 wanted: bad.zip)", result)
	}
	notes, err := store.Notes()
	if err != nil {
		t.Fatal(err)
	}
	titles := map[string]Note{}
	for _, n := range notes {
		titles[n.Title] = n
	}
	for _, want := range []string{"标题甲", "b", "压缩甲", "压缩乙"} {
		if _, ok := titles[want]; !ok {
			t.Errorf("title %q missing; have %v", want, titles)
		}
	}
	if got := titles["b"]; got.Body != "只有正文内容" {
		t.Errorf("fallback title body = %q", got.Body)
	}
	if got := titles["标题甲"]; got.Body != "正文甲" {
		t.Errorf("titled body = %q (heading line must be stripped)", got.Body)
	}
}

func TestImportNotesRoundTrip(t *testing.T) {
	store := openTestStore(t)
	now := time.Now()
	for _, n := range []Note{
		{ID: "r1", Title: "往返一", Body: "正文一", UpdatedAt: now},
		{ID: "r2", Title: "往返二", Body: "正文二", UpdatedAt: now},
	} {
		if err := store.SaveNote(&n); err != nil {
			t.Fatal(err)
		}
	}
	blob, err := ExportNotesZip(store.mustNotes(t), now)
	if err != nil {
		t.Fatal(err)
	}
	result, err := ImportNotes(store, []NoteImportFile{{Name: "export.zip", Data: blob}})
	if err != nil {
		t.Fatal(err)
	}
	if result.Imported != 2 || result.Skipped != 0 {
		t.Fatalf("round trip = %+v", result)
	}
	for _, n := range store.mustNotes(t) {
		if n.Title != "往返一" && n.Title != "往返二" {
			t.Errorf("round-tripped title = %q", n.Title)
		}
	}
}

func TestImportNotesQuota(t *testing.T) {
	store := openTestStore(t)
	batch := map[string]string{}
	for i := range 30 {
		batch[string(rune('a'+i))+".md"] = "# 满\n\nx"
	}
	files := make([]NoteImportFile, 0, 18)
	for range 18 { // 18 × 30 = 540 candidates, quota 500
		files = append(files, NoteImportFile{Name: "batch.zip", Data: zipNoteFixture(t, batch)})
	}
	result, err := ImportNotes(store, files)
	if err != nil {
		t.Fatal(err)
	}
	if !result.QuotaFull {
		t.Fatalf("quota not flagged: %+v", result)
	}
	if result.Imported != noteQuota {
		t.Fatalf("imported = %d want %d", result.Imported, noteQuota)
	}
	if result.Skipped != 40 {
		t.Fatalf("skipped = %d want 40", result.Skipped)
	}
}

func (s *Store) mustNotes(t *testing.T) []Note {
	t.Helper()
	notes, err := s.Notes()
	if err != nil {
		t.Fatal(err)
	}
	return notes
}

func TestSearchAllDomains(t *testing.T) {
	store := openTestStore(t)
	plan := &Plan{ID: "p1", SessionID: "s1", Version: 1, Title: "React 入门", Topic: "React 基础",
		Knowledge: KnowledgeMap{Nodes: []KnowledgeNode{
			{SID: "k1", Name: "useState"},
			{SID: "k2", Name: "useEffect"},
		}}}
	if err := store.SavePlan(plan); err != nil {
		t.Fatal(err)
	}
	if err := store.SaveNote(&Note{ID: "n1", Title: "hooks 笔记", Body: "useState 的用法笔记"}); err != nil {
		t.Fatal(err)
	}
	res, err := SearchAll(store, "useState", 10)
	if err != nil {
		t.Fatal(err)
	}
	if res.Plans.Total != 0 {
		t.Errorf("plans hit = %d", res.Plans.Total)
	}
	if res.Nodes.Total != 1 || res.Nodes.Items[0].Name != "useState" || res.Nodes.Items[0].PlanTopic != "React 基础" {
		t.Errorf("nodes = %+v", res.Nodes)
	}
	if res.Notes.Total != 1 || res.Notes.Items[0].Snippet == "" {
		t.Errorf("notes = %+v (body hit needs a snippet)", res.Notes)
	}
	if len(res.KbDocs.Items) != 0 {
		t.Errorf("kb docs should stay empty until S6")
	}
	res, err = SearchAll(store, "React", 10)
	if err != nil {
		t.Fatal(err)
	}
	if res.Plans.Total != 1 {
		t.Errorf("plans hit = %d", res.Plans.Total)
	}
	if res.Nodes.Total != 0 || res.Notes.Total != 0 {
		t.Errorf("over-broad hits: %+v", res)
	}
	if res, _ = SearchAll(store, "", 10); res.Plans.Total != 0 || res.Nodes.Total != 0 || res.Notes.Total != 0 {
		t.Errorf("empty query must return all-empty domains")
	}
}

func TestUsageOverviewSeries(t *testing.T) {
	store := openTestStore(t)
	now := time.Date(2026, 10, 14, 12, 0, 0, 0, time.Local)
	if err := store.AddUsage(now.Format("2006-01-02"), 1.5); err != nil {
		t.Fatal(err)
	}
	if err := store.AddUsage(now.Format("2006-01-02"), 0.5); err != nil {
		t.Fatal(err)
	}
	summary, err := store.UsageOverview(3, 10, now)
	if err != nil {
		t.Fatal(err)
	}
	if summary.Today.Cost != 2 || summary.Today.Runs != 2 {
		t.Errorf("today = %+v", summary.Today)
	}
	if !summary.BudgetEnabled || summary.DailyBudget != 10 {
		t.Errorf("budget = %+v", summary.DailyBudget)
	}
	if len(summary.Daily) != 3 {
		t.Fatalf("series len = %d", len(summary.Daily))
	}
	if summary.Daily[2].Date != now.Format("2006-01-02") || summary.Daily[2].Cost != 2 {
		t.Errorf("series = %+v", summary.Daily)
	}
	if summary.TotalCost != 2 || summary.TotalRuns != 2 {
		t.Errorf("totals = %+v", summary)
	}
	disabled, _ := store.UsageOverview(3, 0, now)
	if disabled.BudgetEnabled {
		t.Error("budget ≤ 0 must disable")
	}
}
