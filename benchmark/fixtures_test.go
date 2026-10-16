package bench

import (
	"archive/zip"
	"bytes"
	"context"
	"fmt"
	"strings"
	"time"

	"reasonix/internal/runtime/tutor"
)

// fixtures_test.go builds every workload deterministically: same bytes every
// run, so before/after comparisons measure code, not entropy.

// TB is the shared face of testing.B and testing.T.
type TB interface {
	Helper()
	TempDir() string
	Fatalf(string, ...any)
}

func mustSave(b TB, err error) {
	b.Helper()
	if err != nil {
		b.Fatalf("fixture write: %v", err)
	}
}

// newStore opens a throwaway store under the runner's temp dir.
func newStore(b TB) *tutor.Store {
	b.Helper()
	store, err := tutor.OpenStore(b.TempDir())
	if err != nil {
		b.Fatalf("OpenStore: %v", err)
	}
	return store
}

// seedPlans writes n plans of nodesPer knowledge nodes each. Every tenth
// node carries the needle so a search has real hits at a stable rate.
func seedPlans(b TB, store *tutor.Store, n, nodesPer int) {
	b.Helper()
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i := range n {
		nodes := make([]tutor.KnowledgeNode, nodesPer)
		for j := range nodes {
			name := fmt.Sprintf("阶段%03d知识点%03d-%s", i, j, strings.Repeat("概念", 4))
			if j%10 == 0 {
				name = fmt.Sprintf("React钩子%03d-%s", j, strings.Repeat("规则", 3))
			}
			nodes[j] = tutor.KnowledgeNode{SID: fmt.Sprintf("n%03d%03d", i, j), Name: name, Difficulty: j % 5}
		}
		stages := make([]tutor.Stage, 6)
		for si := range stages {
			topics := make([]tutor.StageTopic, 4)
			for ti := range topics {
				topics[ti] = tutor.StageTopic{Name: fmt.Sprintf("主题%d-%d状态管理调度", si, ti), MustLearn: ti == 0}
			}
			stages[si] = tutor.Stage{Index: si, Name: fmt.Sprintf("阶段%d", si), Difficulty: si + 1, Topics: topics}
		}
		mustSave(b, store.SavePlan(&tutor.Plan{
			ID: fmt.Sprintf("plan%03d", i), SessionID: fmt.Sprintf("sess%03d", i), Version: 1,
			Title: fmt.Sprintf("学习方案%03d：前端工程化", i), Topic: "前端工程化",
			CreatedAt: now.Add(time.Duration(i) * time.Hour), UpdatedAt: now.Add(time.Duration(i) * time.Hour),
			Knowledge: tutor.KnowledgeMap{Nodes: nodes}, Stages: stages,
		}))
	}
}

// seedSessions writes n sessions.
func seedSessions(b TB, store *tutor.Store, n int) {
	b.Helper()
	for range n {
		_, err := store.CreateSession("基准会话")
		mustSave(b, err)
	}
}

// seedNotes writes n notes, every tenth carrying the needle in its body.
func seedNotes(b TB, store *tutor.Store, n int) {
	b.Helper()
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	for i := range n {
		body := strings.Repeat("本条笔记记录学习过程中的要点与反思。", 12)
		if i%10 == 0 {
			body = "React 钩子使用规则摘录。" + body
		}
		mustSave(b, store.SaveNote(&tutor.Note{
			ID: fmt.Sprintf("note%03d", i), Title: fmt.Sprintf("笔记%03d", i),
			Body: body, CreatedAt: now, UpdatedAt: now,
		}))
	}
}

// htmlDoc assembles a page of n paragraphs wrapped in semantic structure.
func htmlDoc(n int) []byte {
	var sb strings.Builder
	sb.WriteString("<!doctype html><html><head><title>基准页面</title><style>body{margin:0}</style></head><body>")
	sb.WriteString("<script>var tracker=1;while(false){tracker++}</script>")
	for i := range n {
		fmt.Fprintf(&sb, "<div class=\"row r%d\"><p>第%d段：组件设计要点包括接口收敛、状态归属与渲染边界，", i, i)
		fmt.Fprintf(&sb, "实践中需结合 %d 次迭代验证假设并沉淀文档。</p><a href=\"/ref/%d\">相关引用</a></div>", i%7, i)
	}
	sb.WriteString("</body></html>")
	return []byte(sb.String())
}

// pdfDoc lays out pages of ASCII lines as classic Tj text. Objects are
// written flat: loadPDF scans for object headers, so no xref is needed.
func pdfDoc(pages int) []byte {
	var sb strings.Builder
	var kids strings.Builder
	sb.WriteString("%PDF-1.4\n")
	fmt.Fprintf(&sb, "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
	for p := range pages {
		fmt.Fprintf(&kids, "%d 0 R ", 3+2*p)
	}
	fmt.Fprintf(&sb, "2 0 obj\n<< /Type /Pages /Kids [%s] /Count %d >>\nendobj\n", kids.String(), pages)
	font := 3 + 2*pages
	for p := range pages {
		fmt.Fprintf(&sb, "%d 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>\nendobj\n", 3+2*p, font, 4+2*p)
		var content strings.Builder
		content.WriteString("BT /F1 11 Tf 36 760 Td 14 TL\n")
		for l := range 28 {
			fmt.Fprintf(&content, "(Page %d line %d state management boundaries and one way data flow) Tj T*\n", p, l)
		}
		content.WriteString("ET\n")
		fmt.Fprintf(&sb, "%d 0 obj\n<< /Length %d >>\nstream\n%s endstream\nendobj\n", 4+2*p, content.Len(), content.String())
	}
	fmt.Fprintf(&sb, "%d 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n", font)
	sb.WriteString("trailer\n<< /Root 1 0 R >>\n%%EOF\n")
	return []byte(sb.String())
}

// docxDoc packs paragraphs into a minimal wordprocessingml package; the
// parser reads only word/document.xml.
func docxDoc(paragraphs int) []byte {
	var body strings.Builder
	for i := range paragraphs {
		fmt.Fprintf(&body, "<w:p><w:r><w:t>第%d段：需求拆解后按依赖拓扑排序，", i)
		fmt.Fprintf(&body, "里程碑以可验证的交付物为准，风险项单独标注跟踪。</w:t></w:r></w:p>")
	}
	document := `<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>` + body.String() + `</w:body></w:document>`
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	w, err := zw.Create("word/document.xml")
	if err != nil {
		panic(err)
	}
	if _, err := w.Write([]byte(document)); err != nil {
		panic(err)
	}
	if err := zw.Close(); err != nil {
		panic(err)
	}
	return buf.Bytes()
}

// epubDoc packs chapters of htmlDoc output into a minimal EPUB: the marker,
// the container, the package, and the chapter documents.
func epubDoc(chapters int) []byte {
	var opf strings.Builder
	opf.WriteString(`<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
<metadata/><manifest>`)
	for c := range chapters {
		fmt.Fprintf(&opf, "<item id=\"ch%d\" href=\"ch%d.xhtml\" media-type=\"application/xhtml+xml\"/>", c, c)
	}
	opf.WriteString("</manifest><spine>")
	for c := range chapters {
		fmt.Fprintf(&opf, "<itemref idref=\"ch%d\"/>", c)
	}
	opf.WriteString("</spine></package>")
	container := `<?xml version="1.0"?>
<container><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>`
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	entry := func(name, contentType string, data []byte) {
		w, err := zw.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
		if err != nil {
			panic(err)
		}
		_ = contentType
		if _, err := w.Write(data); err != nil {
			panic(err)
		}
	}
	entry("mimetype", "", []byte("application/epub+zip"))
	entry("META-INF/container.xml", "", []byte(container))
	entry("OEBPS/content.opf", "", []byte(opf.String()))
	for c := range chapters {
		entry(fmt.Sprintf("OEBPS/ch%d.xhtml", c), "", htmlDoc(400))
	}
	if err := zw.Close(); err != nil {
		panic(err)
	}
	return buf.Bytes()
}

// parseFile runs ParseMaterial over raw bytes for one filename.
func parseFile(b TB, name string, data []byte) *tutor.ParsedMaterial {
	b.Helper()
	parsed, err := tutor.ParseMaterial(context.Background(), tutor.MaterialInput{
		Kind: tutor.MaterialFile, Source: name, Data: data,
	})
	if err != nil {
		b.Fatalf("ParseMaterial(%s): %v", name, err)
	}
	if len(parsed.Chunks) == 0 {
		b.Fatalf("ParseMaterial(%s): no chunks", name)
	}
	return parsed
}
