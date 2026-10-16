package tutor

import (
	"archive/zip"
	"bytes"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// zipFixture builds an in-memory zip container, mimetype first when present
// (epub convention), remaining entries in sorted order for determinism.
func zipFixture(t *testing.T, entries map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	w := zip.NewWriter(&buf)
	var rest []string
	for name := range entries {
		if name != "mimetype" {
			rest = append(rest, name)
		}
	}
	sort.Strings(rest)
	ordered := rest
	if _, exists := entries["mimetype"]; exists {
		ordered = append([]string{"mimetype"}, rest...)
	}
	for _, name := range ordered {
		f, err := w.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := f.Write([]byte(entries[name])); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

func TestParseDocxParagraphs(t *testing.T) {
	doc := `<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
 <w:body>
  <w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>
  <w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p>
  <w:p><w:r><w:t>   </w:t></w:r></w:p>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>table cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
 </w:body>
</w:document>`
	data := zipFixture(t, map[string]string{"word/document.xml": doc})
	parsed, err := parseDocx(data)
	if err != nil {
		t.Fatalf("parseDocx: %v", err)
	}
	want := "Hello world\nSecond paragraph\ntable cell"
	if parsed.Text != want {
		t.Fatalf("text = %q, want %q", parsed.Text, want)
	}
	if parsed.Meta.SourceType != "docx" || parsed.Meta.Pages != 1 {
		t.Fatalf("meta = %+v", parsed.Meta)
	}
}

func TestParsePptxSlideOrder(t *testing.T) {
	slide := func(paras ...string) string {
		var sb strings.Builder
		sb.WriteString(`<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="a"><p:cSld><p:spTree>`)
		for _, para := range paras {
			sb.WriteString(`<p:sp><p:txBody><a:p><a:r><a:t>`)
			sb.WriteString(para)
			sb.WriteString(`</a:t></a:r></a:p></p:txBody></p:sp>`)
		}
		sb.WriteString(`</p:spTree></p:cSld></p:sld>`)
		return sb.String()
	}
	data := zipFixture(t, map[string]string{
		"ppt/slides/slide1.xml":  slide("first slide"),
		"ppt/slides/slide10.xml": slide("tenth slide"),
		"ppt/slides/slide2.xml":  slide("second slide"),
		"ppt/notes/notes1.xml":   slide("notes must not leak"),
	})
	parsed, err := parsePptx(data)
	if err != nil {
		t.Fatalf("parsePptx: %v", err)
	}
	want := "first slide\nsecond slide\ntenth slide"
	if parsed.Text != want {
		t.Fatalf("text = %q, want %q", parsed.Text, want)
	}
	if parsed.Meta.Pages != 3 {
		t.Fatalf("pages = %d, want 3", parsed.Meta.Pages)
	}
}

func TestParseEpubSpine(t *testing.T) {
	data := zipFixture(t, map[string]string{
		"mimetype": "application/epub+zip",
		"META-INF/container.xml": `<?xml version="1.0"?>
<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>`,
		"OEBPS/content.opf": `<?xml version="1.0"?>
<package xmlns:opf="http://www.idpf.org/2007/opf">
 <manifest>
  <item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
  <item id="ch2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  <item id="css" href="style.css" media-type="text/css"/>
 </manifest>
 <spine><itemref idref="ch2"/><itemref idref="ch1"/></spine>
</package>`,
		"OEBPS/text/ch1.xhtml": `<html><head><title>Chapter One</title><style>body{}</style></head><body><p>Alpha content</p></body></html>`,
		"OEBPS/text/ch2.xhtml": `<html><body><p>Beta content</p></body></html>`,
		"OEBPS/style.css":      "body { color: red }",
	})
	parsed, err := parseEpub(data)
	if err != nil {
		t.Fatalf("parseEpub: %v", err)
	}
	if !strings.Contains(parsed.Text, "Beta content") || !strings.Contains(parsed.Text, "Alpha content") {
		t.Fatalf("text missing chapters: %q", parsed.Text)
	}
	if strings.Index(parsed.Text, "Beta content") > strings.Index(parsed.Text, "Alpha content") {
		t.Fatalf("spine order not respected: %q", parsed.Text)
	}
	if strings.Contains(parsed.Text, "color") {
		t.Fatalf("css leaked into text: %q", parsed.Text)
	}
}

func TestParseEpubRejectsWrongMarker(t *testing.T) {
	data := zipFixture(t, map[string]string{
		"mimetype":               "application/zip",
		"META-INF/container.xml": "<container/>",
	})
	if _, err := parseEpub(data); err == nil {
		t.Fatal("epub without marker parsed")
	}
}

func TestZipGuardRejectsBomb(t *testing.T) {
	entries := map[string]string{}
	for i := range zipMaxEntries + 1 {
		entries["e"+strconv.Itoa(i)+".xml"] = "x"
	}
	data := zipFixture(t, entries)
	if _, err := parseDocx(data); err == nil {
		t.Fatal("entry-count bomb accepted")
	}
}

func TestParseGarbageContainer(t *testing.T) {
	if _, err := parseDocx([]byte("not a zip at all")); err == nil {
		t.Fatal("garbage accepted as docx")
	}
}

func TestParseFileDispatch(t *testing.T) {
	doc := zipFixture(t, map[string]string{"word/document.xml": "<w:document xmlns:w='u'><w:body><w:p><w:r><w:t>hi</w:t></w:r></w:p></w:body></w:document>"})
	parsed, err := parseFile(MaterialInput{Kind: MaterialFile, Source: "notes.DOCX", Data: doc})
	if err != nil {
		t.Fatalf("dispatch: %v", err)
	}
	if parsed.Text != "hi" {
		t.Fatalf("text = %q", parsed.Text)
	}
	if _, err := parseFile(MaterialInput{Kind: MaterialFile, Source: "a.doc", Data: doc}); err == nil {
		t.Fatal("legacy .doc accepted")
	}
}
