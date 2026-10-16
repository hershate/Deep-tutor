package bench

import (
	"strings"
	"testing"
)

// parse_bench_test.go measures material ingestion: HTML pages, plain text,
// PDF text layers, and docx packages, all through the public ParseMaterial.

const (
	epubChapters   = 40    // 40 × 400-paragraph xhtml chapters
	textRepeat     = 24000 // ~2MB of CJK-dense body text
	pdfPages       = 200
	docxParagraphs = 3000
)

// BenchmarkParseEpub drives the HTML extraction engine the way production
// does: epub chapters through htmlToText.
func BenchmarkParseEpub(b *testing.B) {
	doc := epubDoc(epubChapters)
	b.ResetTimer()
	for b.Loop() {
		parsed := parseFile(b, "book.epub", doc)
		if !strings.Contains(parsed.Text, "组件设计要点") {
			b.Fatalf("epub text lost content")
		}
	}
}

func BenchmarkParseText(b *testing.B) {
	body := strings.Repeat("状态归属决定渲染边界，接口收敛降低耦合。", textRepeat/24)
	b.ResetTimer()
	for b.Loop() {
		parseFile(b, "notes.txt", []byte(body))
	}
}

func BenchmarkParsePDF(b *testing.B) {
	doc := pdfDoc(pdfPages)
	b.ResetTimer()
	for b.Loop() {
		parsed := parseFile(b, "book.pdf", doc)
		if !strings.Contains(parsed.Text, "one way data flow") {
			b.Fatalf("pdf text lost content")
		}
	}
}

func BenchmarkParseDocx(b *testing.B) {
	doc := docxDoc(docxParagraphs)
	b.ResetTimer()
	for b.Loop() {
		parseFile(b, "spec.docx", doc)
	}
}
