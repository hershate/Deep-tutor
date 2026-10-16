package tutor

import (
	"archive/zip"
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
)

func kbService(t *testing.T, opt ServiceOptions) *Service {
	t.Helper()
	return NewService(openTestStore(t), opt)
}

func TestKbUploadConfirmParseFlow(t *testing.T) {
	svc := kbService(t, ServiceOptions{})
	body := "# Rust 入门\n\n所有权是 Rust 的核心概念。"
	doc, err := svc.KbUpload("rust-notes.md", []byte(body))
	if err != nil {
		t.Fatal(err)
	}
	if doc.Status != "pending" || !doc.HasOriginal || doc.Kind != MaterialFile || doc.Ext != "md" {
		t.Fatalf("staged row wrong: %+v", doc)
	}

	// A re-upload of the same bytes reuses the row and says so.
	again, err := svc.KbUpload("别的名字.md", []byte(body))
	if err != nil {
		t.Fatal(err)
	}
	if again.ID != doc.ID || !again.Meta.Deduped {
		t.Fatalf("dedup did not reuse: %+v", again)
	}

	if _, err := svc.KbConfirm(doc.ID); err != nil {
		t.Fatal(err)
	}
	if doc, err = svc.KbParse(context.Background(), doc.ID, ""); err != nil {
		t.Fatal(err)
	}
	if doc.Status != "parsed" || doc.Chars != len([]rune(body)) {
		t.Fatalf("parse wrong: status=%s chars=%d", doc.Status, doc.Chars)
	}
	parsed, err := svc.KbParsedText(doc.ID)
	if err != nil {
		t.Fatal(err)
	}
	if parsed["text"] != body {
		t.Fatalf("parsed text mismatch: %q", parsed["text"])
	}

	// Re-parse (mode switch) is allowed and overwrites.
	if _, err = svc.KbParse(context.Background(), doc.ID, "fast"); err != nil {
		t.Fatal(err)
	}

	renamed, err := svc.KbRename(doc.ID, "Rust 笔记")
	if err != nil {
		t.Fatal(err)
	}
	if renamed.Title != "Rust 笔记" {
		t.Fatalf("rename lost: %+v", renamed)
	}

	if _, _, err := svc.KbRaw(doc.ID); err != nil {
		t.Fatal(err)
	}
	if err := svc.KbDelete(doc.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.store.KbDoc(doc.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("delete left the row: %v", err)
	}
	if _, _, err := svc.KbRaw(doc.ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("raw after delete: %v", err)
	}
}

func TestKbUploadGuards(t *testing.T) {
	svc := kbService(t, ServiceOptions{})
	if _, err := svc.KbUpload("empty.txt", nil); !errors.Is(err, ErrKbEmpty) {
		t.Fatalf("empty upload: %v", err)
	}
	// 50MB is the materials cap; one byte over the line refuses.
	if _, err := svc.KbUpload("big.bin", bytes.Repeat([]byte("x"), maxMaterialBytes+1)); !errors.Is(err, ErrKbTooLarge) {
		t.Fatalf("oversize upload: %v", err)
	}
	// Media kinds stage fine; parsing them states the honest boundary.
	doc, err := svc.KbUpload("clip.mp4", []byte("not really video"))
	if err != nil {
		t.Fatal(err)
	}
	if doc.Kind != MaterialVideo {
		t.Fatalf("kind classify: %s", doc.Kind)
	}
	if _, err = svc.KbConfirm(doc.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = svc.KbParse(context.Background(), doc.ID, ""); !errors.Is(err, ErrUnsupportedFormat) {
		t.Fatalf("media parse: %v", err)
	}
	if doc, err = svc.store.KbDoc(doc.ID); err != nil || doc.Status != "confirmed" {
		t.Fatalf("failed parse changed status: %+v err=%v", doc, err)
	}
}

func TestKbQuota(t *testing.T) {
	svc := kbService(t, ServiceOptions{})
	for i := range KbMaxDocs {
		if _, err := svc.KbAddText("", fmt.Sprintf("第 %d 篇：各不相同的正文 %d", i, i)); err != nil {
			t.Fatalf("fill %d: %v", i, err)
		}
	}
	if _, err := svc.KbAddText("", "超配额的一篇全新正文"); !errors.Is(err, ErrKbQuota) {
		t.Fatalf("quota: %v", err)
	}
	// Dedup still answers past the quota: no new row, no quota spent.
	dupe, err := svc.KbAddText("", "第 0 篇：各不相同的正文 0")
	if err != nil {
		t.Fatal(err)
	}
	if !dupe.Meta.Deduped {
		t.Fatalf("dedup past quota created a row: %+v", dupe)
	}
}

func TestKbTextDoc(t *testing.T) {
	svc := kbService(t, ServiceOptions{})
	text := "标题行当缺省标题\n正文第二行"
	doc, err := svc.KbAddText("", text)
	if err != nil {
		t.Fatal(err)
	}
	if doc.Status != "parsed" || doc.HasOriginal || doc.Kind != MaterialText {
		t.Fatalf("text doc shape: %+v", doc)
	}
	if doc.Title != "标题行当缺省标题" {
		t.Fatalf("title fallback: %q", doc.Title)
	}
	if !strings.HasPrefix(doc.Source, "标题行当缺省标题") {
		t.Fatalf("source summary: %q", doc.Source)
	}
}

func TestKbListAndContentSearch(t *testing.T) {
	svc := kbService(t, ServiceOptions{})
	react, err := svc.KbAddText("React 手册", "React 是一个用于构建用户界面的 JavaScript 库。")
	if err != nil {
		t.Fatal(err)
	}
	goDoc, err := svc.KbAddText("后端笔记", "Go 的 goroutine 是轻量级线程。")
	if err != nil {
		t.Fatal(err)
	}

	docs, err := svc.KbDocList("", "", 0)
	if err != nil || len(docs) != 2 {
		t.Fatalf("list: %v %d", err, len(docs))
	}
	// Newest first.
	if docs[0].ID != goDoc.ID || docs[1].ID != react.ID {
		t.Fatalf("order: %s then %s", docs[0].Title, docs[1].Title)
	}

	// Title hit: whole library.
	hits, err := svc.KbDocList("手册", "", 10)
	if err != nil || len(hits) != 1 || hits[0].ID != react.ID {
		t.Fatalf("title hit: %v %+v", err, hits)
	}
	// Content hit beyond titles.
	hits, err = svc.KbDocList("goroutine", "", 10)
	if err != nil || len(hits) != 1 || hits[0].ID != goDoc.ID {
		t.Fatalf("content hit: %v %+v", err, hits)
	}
	// Kind filter narrows both paths.
	hits, err = svc.KbDocList("React", "url", 10)
	if err != nil || len(hits) != 0 {
		t.Fatalf("kind filter: %v %+v", err, hits)
	}
	if _, err := svc.KbDocList("", "ebook", 10); !errors.Is(err, ErrBadKind) {
		t.Fatalf("bad kind: %v", err)
	}
}

func TestKbContentSearchBounded(t *testing.T) {
	svc := kbService(t, ServiceOptions{})
	// The marker lives in the oldest doc; 50 newer parsed docs push it out
	// of the bounded scan — the honest no-index boundary.
	marker, err := svc.KbAddText("最旧一篇", "独一无二的锚点内容 zebra-unique-marker")
	if err != nil {
		t.Fatal(err)
	}
	_ = marker
	for i := range 50 {
		if _, err := svc.KbAddText("", fmt.Sprintf("第 %d 篇普通正文，内容平淡无奇 %d", i, i)); err != nil {
			t.Fatal(err)
		}
	}
	docs, err := svc.KbDocList("zebra-unique-marker", "", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(docs) != 0 {
		t.Fatalf("bounded scan leaked past 50: %d hits", len(docs))
	}
	// Title search still finds it — titles are never bounded.
	docs, err = svc.KbDocList("最旧一篇", "", 10)
	if err != nil || len(docs) != 1 {
		t.Fatalf("title past bound: %v %d", err, len(docs))
	}
}

func TestKbHitSnippet(t *testing.T) {
	text := strings.Repeat("前奏。", 40) + "关键句在这里出现" + strings.Repeat("尾声。", 40)
	snip := kbHitSnippet(text, "关键句")
	if !strings.Contains(snip, "关键句") || !strings.HasPrefix(snip, "…") || !strings.HasSuffix(snip, "…") {
		t.Fatalf("snippet shape: %q", snip)
	}
	// Whitespace folds to one line.
	if got := kbHitSnippet("第一行\n第二行  有 目标词", "目标词"); strings.Contains(got, "\n") || !strings.Contains(got, "目标词") {
		t.Fatalf("fold: %q", got)
	}
	if kbHitSnippet("没有命中", "目标词") != "" {
		t.Fatal("miss should be empty")
	}
	// Case-insensitive on ASCII.
	if kbHitSnippet("Say HELLO again", "hello") == "" {
		t.Fatal("case-insensitive miss")
	}
}

func TestKbChatCitationsAndRecords(t *testing.T) {
	store := openTestStore(t)
	var citedID string
	var llmCalls int
	llm := &fakeLLM{t: t, reply: func(system string, call int) string {
		if !strings.Contains(system, "知识库问答") {
			t.Fatal("chat call used a foreign prompt")
		}
		llmCalls++
		// used_doc_ids carries one real id and one hallucinated id; only
		// the real one may survive as a citation.
		return fmt.Sprintf(`{"answer":"据《Rust 手册》：所有权。","used_doc_ids":["%s","deadbeef"]}`, citedID)
	}}
	svc := NewService(store, ServiceOptions{
		Provider: func() (LLM, error) { return llm, nil },
		Pricer:   func(model string, u Usage) float64 { return 0.25 },
	})

	rust, err := svc.KbAddText("Rust 手册", "所有权是 Rust 最独特的概念：每个值有且仅有一个所有者。")
	if err != nil {
		t.Fatal(err)
	}
	citedID = rust.ID
	pending, err := svc.KbUpload("not-yet-parsed.md", []byte("# 未解析\n没有解析文本"))
	if err != nil {
		t.Fatal(err)
	}

	result, err := svc.KbChat(context.Background(), "什么是所有权？", []string{rust.ID, pending.ID})
	if err != nil {
		t.Fatal(err)
	}
	if result.ContextDocs != 1 {
		t.Fatalf("context docs: %d", result.ContextDocs)
	}
	if len(result.Citations) != 1 || result.Citations[0].DocID != rust.ID ||
		result.Citations[0].Title != "Rust 手册" {
		t.Fatalf("citations: %+v", result.Citations)
	}
	if len(result.OmittedTitles) != 1 || result.OmittedTitles[0] != pending.Title {
		t.Fatalf("omitted: %+v", result.OmittedTitles)
	}
	if llmCalls != 1 {
		t.Fatalf("llm calls: %d", llmCalls)
	}

	// Metering landed on today's ledger.
	usage, err := store.UsageOfDay(svc.opt.now().Format("2006-01-02"))
	if err != nil || usage.Cost <= 0 || usage.Runs != 1 {
		t.Fatalf("usage ledger: %+v err=%v", usage, err)
	}

	// The record is self-contained and listed; deletion is terminal.
	records, err := svc.KbChatList("", 0, 0)
	if err != nil || records["total"] != 1 {
		t.Fatalf("records: %v %v", err, records["total"])
	}
	items := records["items"].([]KbChatRecord)
	if items[0].Answer == "" || len(items[0].Citations) != 1 {
		t.Fatalf("record shape: %+v", items[0])
	}
	filtered, err := svc.KbChatList("所有权", 0, 0)
	if err != nil || filtered["total"] != 1 {
		t.Fatalf("q filter: %v", err)
	}
	filtered, err = svc.KbChatList("完全不相关的问题词", 0, 0)
	if err != nil || filtered["total"] != 0 {
		t.Fatalf("q filter miss: %v", err)
	}
	if err := svc.KbChatDelete(items[0].ID); err != nil {
		t.Fatal(err)
	}
	if err := svc.KbChatDelete(items[0].ID); !errors.Is(err, ErrNotFound) {
		t.Fatalf("re-delete: %v", err)
	}
}

func TestKbChatGuards(t *testing.T) {
	llm := &fakeLLM{t: t, reply: func(string, int) string { return `{"answer":"a","used_doc_ids":[]}` }}
	store := openTestStore(t)
	svc := NewService(store, ServiceOptions{
		Provider: func() (LLM, error) { return llm, nil },
	})
	if _, err := svc.KbChat(context.Background(), "  ", nil); !errors.Is(err, ErrKbEmpty) {
		t.Fatalf("empty question: %v", err)
	}
	if _, err := svc.KbChat(context.Background(), strings.Repeat("长", kbQuestionMaxChars+1), nil); !errors.Is(err, ErrKbTooLarge) {
		t.Fatalf("long question: %v", err)
	}
	if _, err := svc.KbChat(context.Background(), "问题", make([]string, kbChatMaxDocs+1)); !errors.Is(err, ErrKbTooLarge) {
		t.Fatalf("too many docs: %v", err)
	}
	// An empty library has nothing to answer from.
	if _, err := svc.KbChat(context.Background(), "问题", nil); !errors.Is(err, ErrKbNoDocs) {
		t.Fatalf("no docs: %v", err)
	}
	if _, err := svc.KbChat(context.Background(), "问题", []string{"missing"}); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing doc: %v", err)
	}

	// Budget guard fires before any model call.
	filled := NewService(openTestStore(t), ServiceOptions{
		Provider:    func() (LLM, error) { return llm, nil },
		DailyBudget: 1,
	})
	if _, err := filled.KbAddText("有货", "库里有内容，可以注入上下文"); err != nil {
		t.Fatal(err)
	}
	if err := filled.store.AddUsage(filled.opt.now().Format("2006-01-02"), 1.0); err != nil {
		t.Fatal(err)
	}
	if _, err := filled.KbChat(context.Background(), "问题", nil); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("budget: %v", err)
	}

	// No provider is a plain refusal, not a fabricated answer.
	noLLM := NewService(openTestStore(t), ServiceOptions{})
	if _, err := noLLM.KbAddText("有货", "库里有内容"); err != nil {
		t.Fatal(err)
	}
	if _, err := noLLM.KbChat(context.Background(), "问题", nil); !errors.Is(err, ErrNoLLM) {
		t.Fatalf("no provider: %v", err)
	}
}

func TestKbChatContextCaps(t *testing.T) {
	svc := NewService(openTestStore(t), ServiceOptions{})
	// One doc well past the per-doc cap: injected text is clipped and marked.
	var sb strings.Builder
	for runes := 0; runes <= kbChatDocChars+100; runes += 14 {
		sb.WriteString("很长的正文内容，重复以越过单篇注入上限。") // 14 runes per append
	}
	if _, err := svc.KbAddText("长文", sb.String()); err != nil {
		t.Fatal(err)
	}
	payload, omitted := svc.assembleKbContext(mustDocs(t, svc))
	if len(payload) != 1 || len(omitted) != 0 {
		t.Fatalf("payload: %d omitted: %d", len(payload), len(omitted))
	}
	runes := []rune(payload[0].Text)
	if len(runes) > kbChatDocChars+len("…[已截断]") || !strings.HasSuffix(payload[0].Text, "…[已截断]") {
		t.Fatalf("per-doc clip: %d runes, tail %q", len(runes), payload[0].Text[len(payload[0].Text)-20:])
	}
}

func mustDocs(t *testing.T, svc *Service) []KbDoc {
	t.Helper()
	docs, err := svc.store.KbDocs()
	if err != nil {
		t.Fatal(err)
	}
	return docs
}

func TestKbExportZip(t *testing.T) {
	svc := NewService(openTestStore(t), ServiceOptions{})
	if _, _, err := svc.KbExportZip(nil); err == nil {
		t.Fatal("empty export should refuse")
	}
	textDoc, err := svc.KbAddText("纯文本文档", "只有解析文本，没有原件。")
	if err != nil {
		t.Fatal(err)
	}
	fileDoc, err := svc.KbUpload("指南.md", []byte("# 指南\n双件套：原件加解析文本。"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err = svc.KbConfirm(fileDoc.ID); err != nil {
		t.Fatal(err)
	}
	if _, err = svc.KbParse(context.Background(), fileDoc.ID, ""); err != nil {
		t.Fatal(err)
	}

	path, count, err := svc.KbExportZip(nil)
	if err != nil || count != 2 {
		t.Fatalf("export: %v count=%d", err, count)
	}
	defer os.Remove(path)
	names := readZipNames(t, path)
	want := map[string]bool{"index.md": false,
		"originals/" + fileDoc.ID + "-指南.md":  false,
		"parsed/" + fileDoc.ID + "-指南.txt":    false,
		"parsed/" + textDoc.ID + "-纯文本文档.txt": false,
	}
	for _, name := range names {
		if _, ok := want[name]; ok {
			want[name] = true
		}
	}
	for name, seen := range want {
		if !seen {
			t.Errorf("zip missing %s (have %v)", name, names)
		}
	}

	// The ids filter picks exactly one document.
	path, count, err = svc.KbExportZip([]string{textDoc.ID})
	if err != nil || count != 1 {
		t.Fatalf("filtered export: %v count=%d", err, count)
	}
	defer os.Remove(path)
	for _, name := range readZipNames(t, path) {
		if strings.Contains(name, fileDoc.ID) {
			t.Errorf("filter leaked: %s", name)
		}
	}
}

func readZipNames(t *testing.T, path string) []string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	zr, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, f := range zr.File {
		names = append(names, f.Name)
	}
	return names
}

func TestKbGenerateFromDocs(t *testing.T) {
	store := openTestStore(t)
	svc := NewService(store, ServiceOptions{})
	doc, err := svc.KbAddText("Rust 手册", "所有权是 Rust 的核心概念。")
	if err != nil {
		t.Fatal(err)
	}
	staged, err := svc.KbUpload("待解析.md", []byte("# 待解析\n未确认"))
	if err != nil {
		t.Fatal(err)
	}

	if _, _, err := svc.KbGenerateFromDocs([]string{staged.ID}, ""); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unparsed doc: %v", err)
	}
	if _, _, err := svc.KbGenerateFromDocs(nil, ""); !errors.Is(err, ErrKbEmpty) {
		t.Fatalf("empty selection: %v", err)
	}

	sid, count, err := svc.KbGenerateFromDocs([]string{doc.ID}, "我的 KB 方案")
	if err != nil || count != 1 {
		t.Fatalf("generate: %v count=%d", err, count)
	}
	sess, err := store.Session(sid)
	if err != nil || sess.Title != "我的 KB 方案" {
		t.Fatalf("session: %v %+v", err, sess)
	}
	materials, err := store.Materials(sid)
	if err != nil || len(materials) != 1 {
		t.Fatalf("materials: %v %d", err, len(materials))
	}
	m := materials[0]
	if m.Status != "parsed" || m.Meta["kb_document_id"] != doc.ID {
		t.Fatalf("material shape: %+v", m)
	}
	// The pipeline's parse step aggregates the seeded text unchanged.
	out, _ := store.materialsParsedOutput(sid)
	if out.Status != CallOK {
		t.Fatalf("aggregate status: %+v", out)
	}
	parsed, _ := store.ReadMaterialParsed(sid, m.ID)
	if !strings.Contains(parsed, "所有权") {
		t.Fatalf("seeded text missing: %q", parsed)
	}
}

func TestSearchAllKbDocsDomain(t *testing.T) {
	store := openTestStore(t)
	svc := NewService(store, ServiceOptions{})
	titled, err := svc.KbAddText("搜索标题命中", "正文完全无关")
	if err != nil {
		t.Fatal(err)
	}
	inner, err := svc.KbAddText("平淡标题", "正文里藏着搜索目标词 target-word")
	if err != nil {
		t.Fatal(err)
	}
	results, err := SearchAll(store, "target-word", 10)
	if err != nil {
		t.Fatal(err)
	}
	if results.KbDocs.Total != 1 || len(results.KbDocs.Items) != 1 {
		t.Fatalf("content domain: %+v", results.KbDocs)
	}
	if results.KbDocs.Items[0].ID != inner.ID || !strings.Contains(results.KbDocs.Items[0].Snippet, "target-word") {
		t.Fatalf("content hit: %+v", results.KbDocs.Items[0])
	}

	results, err = SearchAll(store, "搜索标题", 10)
	if err != nil {
		t.Fatal(err)
	}
	if results.KbDocs.Total != 1 || results.KbDocs.Items[0].ID != titled.ID {
		t.Fatalf("title domain: %+v", results.KbDocs)
	}
}
