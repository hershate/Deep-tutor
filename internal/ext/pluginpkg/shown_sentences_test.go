package pluginpkg

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

const unterminated = "\x1b]0;"

func requireShown(t *testing.T, got string) {
	t.Helper()
	if strings.ContainsRune(got, 0x1b) || !strings.Contains(got, `\u{1b}]0;`) {
		t.Fatalf("an unterminated escape reached the sentence raw: %q", got)
	}
}

func TestShadowWarningsShowPathsAsLiterals(t *testing.T) {
	p := Package{Root: "/pkg"}
	_, agentWarnings := p.selectAgentRefs([]AgentRef{
		{Name: "a", Path: "/pkg/one" + unterminated}, {Name: "a", Path: "/pkg/two" + unterminated},
	})
	if len(agentWarnings) != 1 || !strings.HasSuffix(agentWarnings[0], "is shadowed") {
		t.Fatalf("agent warnings = %q", agentWarnings)
	}
	requireShown(t, agentWarnings[0])
	_, skillWarnings := p.selectSkillRefs([]SkillRef{
		{Name: "s", Path: "/pkg/one" + unterminated}, {Name: "s", Path: "/pkg/two" + unterminated},
	})
	if len(skillWarnings) != 1 || !strings.HasSuffix(skillWarnings[0], "is shadowed") {
		t.Fatalf("skill warnings = %q", skillWarnings)
	}
	requireShown(t, skillWarnings[0])
}

func TestIncompatibleWarningShowsTheErrorAsALiteralAndKeepsItsRemediation(t *testing.T) {
	got := incompatibleWarning("demo", errors.New("bad manifest"+unterminated), "repair or reinstall the plugin")
	requireShown(t, got)
	if !strings.HasSuffix(got, "remediation: repair or reinstall the plugin") {
		t.Fatalf("the sentence lost its tail: %q", got)
	}
}

func TestInstalledNameAndDecodeErrorsAreShownAsLiterals(t *testing.T) {
	requireShown(t, migratedWarning("demo"+unterminated))
	requireShown(t, migrateRemediation("demo"+unterminated))
	var v struct{}
	err := strictDecode([]byte(`{"bad`+unterminated+`": 1}`), &v, "plugin.json")
	if err == nil {
		t.Fatal("an unknown field must fail")
	}
	if strings.ContainsRune(err.Error(), 0x1b) {
		t.Fatalf("a raw escape reached the decode error: %q", err.Error())
	}
	var syn *json.SyntaxError
	var ute *json.UnmarshalTypeError
	if errors.As(err, &syn) || errors.As(err, &ute) {
		return
	}
	if errors.Unwrap(err) == nil {
		t.Fatal("the decoder's error is not in the chain")
	}
}

func TestStrictDecodeErrorsStayInTheChain(t *testing.T) {
	var v struct {
		N int `json:"n"`
	}
	err := strictDecode([]byte(`{"n":"x"}`), &v, "plugin.json")
	var typed *json.UnmarshalTypeError
	if !errors.As(err, &typed) {
		t.Fatalf("errors.As cannot reach the decoder's error through %v", err)
	}
}
