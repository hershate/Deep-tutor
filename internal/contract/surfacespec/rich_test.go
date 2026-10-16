package surfacespec

import (
	"strings"
	"testing"
)

func TestRichTaskSurfaces(t *testing.T) {
	const raw = `{"spec_version":1,"body":[{"kind":"map","key":"stops","label":"Trip","field":{"type":"multichoice","required":true,"options":[{"value":"a","label":"Museum","group":"Day 1","location":{"latitude":35.7,"longitude":139.7}},{"value":"b","label":"Park","location":{"latitude":35.8,"longitude":139.8}}]}},{"kind":"comparison","key":"product","label":"Compare","field":{"type":"choice","options":[{"value":"p","label":"Camera","price":400,"currency":"USD","facts":[{"label":"Weight","value":"300 g"}]}]}},{"kind":"dashboard","label":"Revenue","dataset":{"categories":["Jan","Feb"],"series":[{"id":"revenue","label":"Revenue","unit":"USD","values":[10,20]}]}}]}`
	s, err := Parse([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.ParseAnswers([]byte(`{"stops":["b","a"],"product":"p"}`)); err != nil {
		t.Fatal(err)
	}
	for _, bad := range []string{
		strings.Replace(raw, `35.7`, `91`, 1),
		strings.Replace(raw, `139.7`, `181`, 1),
		strings.Replace(raw, `"latitude":35.7,`, ``, 1),
		strings.Replace(raw, `"longitude":139.7`, `"longitude":139.7,"url":"https://invalid.test"`, 1),
		strings.Replace(raw, `"values":[10,20]`, `"values":[10]`, 1),
		strings.Replace(raw, `"price":400`, `"price":-1`, 1),
		strings.Replace(raw, `"unit":"USD"`, `"unit":"USD","fetch":"https://invalid.test"`, 1),
	} {
		if _, err := Parse([]byte(bad)); err == nil {
			t.Fatalf("accepted invalid rich spec: %s", bad)
		}
	}
	s.Redact(func(v string) string { return strings.ReplaceAll(v, "Revenue", "hidden") })
	canonical, _ := s.Canonical()
	if strings.Contains(string(canonical), "Revenue") {
		t.Fatal("dataset labels escaped redaction")
	}
	if _, err := Parse(canonical); err != nil {
		t.Fatal(err)
	}
}

func TestRichImageReferences(t *testing.T) {
	const raw = `{"spec_version":1,"body":[{"kind":"comparison","key":"product","label":"Compare","field":{"type":"choice","options":[{"value":"a","label":"Camera","image":{"path":"images/camera.png","alt":"Camera front"}}]}}]}`
	s, err := Parse([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	s.Redact(func(v string) string { return strings.ReplaceAll(v, "Camera front", "Product photo") })
	b, _ := s.Canonical()
	if !strings.Contains(string(b), "Product photo") {
		t.Fatal("image description escaped redaction")
	}
	for _, invalid := range []string{"../camera.png", "/camera.png", "https://example.com/a.png", "C:/camera.png", "images/../a.png", "images//a.png", "images/a.svg", "images/a.png?x=1", "images/%2e%2e/a.png"} {
		if _, err := Parse([]byte(strings.Replace(raw, "images/camera.png", invalid, 1))); err == nil {
			t.Fatalf("accepted %s", invalid)
		}
	}
}

func TestImageSource(t *testing.T) {
	raw := `{"spec_version":1,"body":[{"kind":"comparison","key":"product","label":"Compare","field":{"type":"choice","options":[{"value":"a","label":"Camera","image":{"path":"images/camera.png","alt":"Camera front","source":"https://example.com/product"}}]}}]}`
	if _, err := Parse([]byte(raw)); err != nil {
		t.Fatal(err)
	}
	for _, source := range []string{"javascript:alert(1)", "file:///tmp/a", "//example.com/a", "https://user:pass@example.com/a"} {
		if _, err := Parse([]byte(strings.Replace(raw, "https://example.com/product", source, 1))); err == nil {
			t.Fatalf("accepted %s", source)
		}
	}
}
