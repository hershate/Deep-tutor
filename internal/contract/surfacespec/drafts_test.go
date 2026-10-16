package surfacespec

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestDraftAndDiffContract(t *testing.T) {
	raw := `{"spec_version":1,"body":[{"kind":"editable_text","key":"draft","label":"Draft","field":{"type":"string","required":true,"default":"  before\n"}},{"kind":"diff_preview","label":"Changes","diff":{"before":"  before\n","after":"  after\n"}}]}`
	s, err := Parse([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	const draft = "  edited\n\n@file /command <script>\t "
	value, _ := json.Marshal(draft)
	answers, err := s.ParseAnswers(json.RawMessage(`{"draft":` + string(value) + `}`))
	if err != nil {
		t.Fatal(err)
	}
	var got string
	if err := json.Unmarshal(answers["draft"], &got); err != nil || got != draft {
		t.Fatalf("draft changed: %q %v", got, err)
	}
	for _, bad := range []string{
		strings.Replace(raw, `"type":"string"`, `"type":"number"`, 1),
		strings.Replace(raw, `"after":"  after\n"`, `"after":"  after\n","apply":true`, 1),
		strings.Replace(raw, `"kind":"diff_preview"`, `"kind":"diff_preview","actionId":"approve"`, 1),
		strings.Replace(raw, `"before":"  before\n"`, `"before":"`+strings.Repeat("x", MaxText+1)+`"`, 1),
		strings.Replace(raw, `"before":"  before\n",`, "", 1),
	} {
		if _, err := Parse([]byte(bad)); err == nil {
			t.Fatalf("accepted %s", bad)
		}
	}
	s.Redact(func(v string) string { return strings.ReplaceAll(v, "before", "hidden") })
	canonical, err := s.Canonical()
	if err != nil || strings.Contains(string(canonical), "before\\n") {
		t.Fatalf("display redaction: %s %v", canonical, err)
	}
	if _, err := Parse(canonical); err != nil {
		t.Fatal(err)
	}
}
