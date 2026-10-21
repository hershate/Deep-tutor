package tutor

import (
	"fmt"
	"strings"
	"testing"
)

// A document's streams share one decompression budget. The per-stream cap
// bounds one stream; the sum is what a bomb is made of.
func TestPdfInflateBudgetChargesEachStreamOnce(t *testing.T) {
	f := &pdfFile{objs: map[int]pdfObj{}}
	f.inflateLeft.Store(1024)

	first := &pdfStream{}
	if err := f.chargeInflate(first, 600); err != nil {
		t.Fatalf("first stream: %v", err)
	}
	// The same stream decoded again (a font's ToUnicode is decoded once per
	// page) must not be charged twice.
	if err := f.chargeInflate(first, 600); err != nil {
		t.Fatalf("the same stream charged twice: %v", err)
	}
	second := &pdfStream{}
	if err := f.chargeInflate(second, 600); err == nil {
		t.Fatal("the document budget was not enforced")
	}
}

// The page tree is walked recursively; a chain of nested Pages nodes is a
// stack-depth lever, and a document with a million pages is a memory
// amplifier.
func TestPdfPageTreeDepthAndCountAreBounded(t *testing.T) {
	deep := &pdfBuilder{}
	deep.add("<< /Type /Catalog /Pages 2 0 R >>")
	levels := pdfMaxPageDepth + 5
	// The chain: object 1 is the catalog, objects 2..levels+1 are nested
	// Pages nodes, object levels+2 is the leaf page and levels+3 its
	// content stream.
	for i := range levels {
		deep.add(fmt.Sprintf("<< /Type /Pages /Kids [%d 0 R] /Count 1 >>", 3+i))
	}
	deep.add(fmt.Sprintf("<< /Type /Page /Parent 2 0 R /Contents %d 0 R >>", levels+3))
	deep.add("<< /Filter /FlateDecode /Length 0 >>\nstream")
	data := deep.build(t, map[int]string{levels + 3: "BT ET"})

	file, err := loadPDF(data)
	if err != nil {
		t.Fatalf("loadPDF: %v", err)
	}
	if _, err := file.pages(); err == nil {
		t.Fatal("a page tree nested past the depth cap was accepted")
	} else if !strings.Contains(err.Error(), "nests deeper") {
		t.Fatalf("unexpected refusal: %v", err)
	}
}

func TestPdfPageCountIsBounded(t *testing.T) {
	b := &pdfBuilder{}
	b.add("<< /Type /Catalog /Pages 2 0 R >>")
	kids := make([]string, 0, pdfMaxPages+2)
	for i := range pdfMaxPages + 1 {
		kids = append(kids, fmt.Sprintf("%d 0 R", 3+i))
	}
	b.add("<< /Type /Pages /Kids [" + strings.Join(kids, " ") + "] /Count " + fmt.Sprint(pdfMaxPages+1) + " >>")
	for range pdfMaxPages + 1 {
		b.add(fmt.Sprintf("<< /Type /Page /Parent 2 0 R /Contents %d 0 R >>", 3+pdfMaxPages+2))
	}
	b.add("<< /Filter /FlateDecode /Length 0 >>\nstream")
	data := b.build(t, map[int]string{3 + pdfMaxPages + 2: "BT ET"})

	file, err := loadPDF(data)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := file.pages(); err == nil {
		t.Fatalf("a document with %d pages was accepted", pdfMaxPages+1)
	} else if !strings.Contains(err.Error(), "more than") {
		t.Fatalf("unexpected refusal: %v", err)
	}
}
