package tutor

import (
	"bytes"
	"compress/zlib"
	"fmt"
	"strings"
	"testing"
)

// pdfBuilder assembles a small but valid classic-xref PDF: objects in order,
// a real cross-reference table, and Flate-compressed streams.
type pdfBuilder struct {
	objects []string // bodies, index = object number - 1
}

func (b *pdfBuilder) add(body string) int {
	b.objects = append(b.objects, body)
	return len(b.objects)
}

func flateBytes(t *testing.T, raw string) []byte {
	t.Helper()
	var buf bytes.Buffer
	zw := zlib.NewWriter(&buf)
	if _, err := zw.Write([]byte(raw)); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// build writes the file, splicing each stream object's compressed payload
// between its "stream" marker and "endstream".
func (b *pdfBuilder) build(t *testing.T, streamPayloads map[int]string) []byte {
	t.Helper()
	var out bytes.Buffer
	out.WriteString("%PDF-1.4\n")
	offsets := make([]int, len(b.objects)+1)
	for i, body := range b.objects {
		offsets[i+1] = out.Len()
		objNum := i + 1
		payload, isStream := streamPayloads[objNum]
		fmt.Fprintf(&out, "%d 0 obj\n", objNum)
		if isStream {
			// body ends with the bare "stream" word; splice payload after it.
			idx := strings.LastIndex(body, "stream")
			out.WriteString(body[:idx+len("stream")])
			out.WriteString("\n")
			out.Write(flateBytes(t, payload))
			out.WriteString("\nendstream\nendobj\n")
		} else {
			out.WriteString(body)
			out.WriteString("\nendobj\n")
		}
	}
	xref := out.Len()
	fmt.Fprintf(&out, "xref\n0 %d\n", len(b.objects)+1)
	out.WriteString("0000000000 65535 f \n")
	for i := 1; i <= len(b.objects); i++ {
		fmt.Fprintf(&out, "%010d 00000 n \n", offsets[i])
	}
	fmt.Fprintf(&out, "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n", len(b.objects)+1, xref)
	return out.Bytes()
}

// simplePDF builds a one-page PDF whose content uses a WinAnsi simple font.
func simplePDF(t *testing.T, content string) []byte {
	t.Helper()
	b := &pdfBuilder{}
	b.add("<< /Type /Catalog /Pages 2 0 R >>")                                                      // 1 catalog
	b.add("<< /Type /Pages /Kids [3 0 R] /Count 1 >>")                                              // 2 pages
	b.add("<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>") // 3 page
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream")                                           // 4 contents
	b.add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")                                 // 5 font
	payload := map[int]string{4: content}
	return b.build(t, payload)
}

func TestPDFSimpleFont(t *testing.T) {
	content := "BT /F1 12 Tf 72 720 Td (Hello pdf world) Tj ET"
	parsed, err := parsePDF(simplePDF(t, content))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	if parsed.Text != "Hello pdf world" {
		t.Fatalf("text = %q", parsed.Text)
	}
	if parsed.Meta.Pages != 1 || parsed.Meta.SourceType != "pdf" {
		t.Fatalf("meta = %+v", parsed.Meta)
	}
}

func TestPDFMultipleLines(t *testing.T) {
	content := "BT /F1 12 Tf 14.4 TL 72 720 Td (line one) Tj T* (line two) Tj 0 -20 Td (line three) Tj ET"
	parsed, err := parsePDF(simplePDF(t, content))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	want := "line one\nline two\nline three"
	if parsed.Text != want {
		t.Fatalf("text = %q, want %q", parsed.Text, want)
	}
}

func TestPDFHexAndTJ(t *testing.T) {
	content := "BT /F1 12 Tf 72 720 Td <48 65 78> Tj [(TJ )-2(text)] TJ ET"
	parsed, err := parsePDF(simplePDF(t, content))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	if parsed.Text != "HexTJ text" && parsed.Text != "HexTJtext" {
		t.Fatalf("text = %q", parsed.Text)
	}
}

// cidPDF builds a page whose font carries a ToUnicode CMap mapping 2-byte
// codes, the usual shape of CJK documents.
func cidPDF(t *testing.T, content string, cmap string) []byte {
	t.Helper()
	b := &pdfBuilder{}
	b.add("<< /Type /Catalog /Pages 2 0 R >>")
	b.add("<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
	b.add("<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /Font << /F9 5 0 R >> >> >>")
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream")
	b.add(fmt.Sprintf("<< /Type /Font /Subtype /Type0 /BaseFont /Test /ToUnicode %d 0 R >>", 6))
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream")
	payload := map[int]string{4: content, 6: cmap}
	return b.build(t, payload)
}

func TestPDFToUnicodeCID(t *testing.T) {
	cmap := `/CIDInit /ProcSet findresource begin
1 begincodespacerange
<0000> <ffff>
endcodespacerange
2 beginbfchar
<0001> <5b66>
<0002> <4e60>
endbfchar
1 beginbfrange
<0010> <0012> <8ba1>
endbfrange
CMapName currentdict /CMap defineresource pop end`
	content := "BT /F9 12 Tf 72 720 Td <00010002> Tj T* <001000110012> Tj ET"
	parsed, err := parsePDF(cidPDF(t, content, cmap))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	// 0001/0002 map through bfchar; 0010..0012 walk the bfrange base.
	want := "学习\n\u8ba1\u8ba2\u8ba3"
	if parsed.Text != want {
		t.Fatalf("text = %q, want %q", parsed.Text, want)
	}
}

func TestPDFScannedFileHasNoText(t *testing.T) {
	content := "q 1 0 0 1 0 0 cm /Im0 Do Q"
	if _, err := parsePDF(simplePDF(t, content)); err == nil {
		t.Fatal("image-only pdf produced text")
	}
}

func TestPDFEncryptedRejected(t *testing.T) {
	raw := strings.Replace(string(simplePDF(t, "BT ET")), "/BaseFont", "/Encrypt << >> /BaseFont", 1)
	if _, err := parsePDF([]byte(raw)); err == nil {
		t.Fatal("encrypted pdf accepted")
	}
}

func TestPDFCIDWithoutToUnicodeIsHonest(t *testing.T) {
	// A CID font with no ToUnicode map carries glyph indices, not text.
	// Decoding them through CP1252 would invent mojibake; the honest answer
	// is an empty layer that surfaces the "scanned or encoded" error.
	b := &pdfBuilder{}
	b.add("<< /Type /Catalog /Pages 2 0 R >>")
	b.add("<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
	b.add("<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /Font << /F2 5 0 R >> >> >>")
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream")
	b.add("<< /Type /Font /Subtype /Type0 /BaseFont /Test >>")
	payload := map[int]string{4: "BT /F2 12 Tf 72 720 Td <004100420043> Tj ET"}
	if _, err := parsePDF(b.build(t, payload)); err == nil {
		t.Fatal("cid font without ToUnicode produced text")
	}
}

func TestPDFWinAnsiAccents(t *testing.T) {
	// CP1252 single-byte text with a real accent decodes intact.
	content := "BT /F1 12 Tf 72 720 Td (caf\xe9 na\xefve) Tj ET"
	parsed, err := parsePDF(simplePDF(t, content))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	if parsed.Text != "café naïve" {
		t.Fatalf("text = %q", parsed.Text)
	}
}

func TestPDFFilterArrayAndMultiplePages(t *testing.T) {
	b := &pdfBuilder{}
	b.add("<< /Type /Catalog /Pages 2 0 R >>")
	b.add("<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>")
	b.add("<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
	b.add("<< /Filter [/FlateDecode] /Length 0 >>\nstream")
	b.add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
	b.add("<< /Type /Page /Parent 2 0 R /Contents 7 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream")
	payload := map[int]string{
		4: "BT /F1 12 Tf 72 720 Td (page one) Tj ET",
		7: "BT /F1 12 Tf 72 720 Td (page two) Tj ET",
	}
	parsed, err := parsePDF(b.build(t, payload))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	if parsed.Meta.Pages != 2 {
		t.Fatalf("pages = %d", parsed.Meta.Pages)
	}
	if parsed.Text != "page one\n\npage two" {
		t.Fatalf("text = %q", parsed.Text)
	}
}

// objstmPDF packs the structural objects into an object stream — the modern
// writer shape; the scanner must expand them without classic declarations.
func objstmPDF(t *testing.T, content string) []byte {
	t.Helper()
	entries := []struct {
		num  int
		body string
	}{
		{2, "<< /Type /Catalog /Pages 3 0 R >>"},
		{3, "<< /Type /Pages /Kids [4 0 R] /Count 1 >>"},
		{4, "<< /Type /Page /Parent 3 0 R /Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> >>"},
		{7, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"},
	}
	var header, body strings.Builder
	for _, e := range entries {
		fmt.Fprintf(&header, "%d %d ", e.num, body.Len())
		body.WriteString(e.body + " ")
	}
	stmBody := header.String() + body.String()

	b := &pdfBuilder{}
	// On-disk layout: 1=ObjStm, 2-4=null placeholders the expansion replaces,
	// 5=page contents, 6-7=null (7 becomes the font via expansion).
	b.add(fmt.Sprintf("<< /Type /ObjStm /N %d /First %d /Filter /FlateDecode /Length 0 >>\nstream", len(entries), header.Len()))
	b.add("null")                                         // 2
	b.add("null")                                         // 3
	b.add("null")                                         // 4
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream") // 5: page contents
	b.add("null")                                         // 6
	b.add("null")                                         // 7
	payload := map[int]string{1: stmBody, 5: content}
	return b.build(t, payload)
}

func TestPDFObjectStream(t *testing.T) {
	parsed, err := parsePDF(objstmPDF(t, "BT /F1 12 Tf 72 720 Td (packed away) Tj ET"))
	if err != nil {
		t.Fatalf("parsePDF: %v", err)
	}
	if parsed.Text != "packed away" {
		t.Fatalf("text = %q", parsed.Text)
	}
}
