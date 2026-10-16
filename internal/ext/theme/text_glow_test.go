package theme

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestTextGlowSurvivesThemeLoading(t *testing.T) {
	for _, tc := range []struct {
		name, field, want string
		warnings          int
	}{
		{"legacy", ``, "", 0},
		{"null", `"textGlow":null`, "", 0},
		{"default", `"textGlow":"default"`, "default", 0},
		{"disabled", `"textGlow":"none"`, "none", 0},
		{"unknown", `"textGlow":"bright"`, "", 1},
		{"css", `"textGlow":"none; color: red"`, "", 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			raw := strings.TrimSuffix(minimal, "}") + `,"background":{` + tc.field + `}}`
			pack, err := decode([]byte(raw), "glow-probe")
			if err != nil {
				t.Fatal(err)
			}
			body, err := json.Marshal(pack.Background)
			if err != nil {
				t.Fatal(err)
			}
			var bg map[string]any
			if err := json.Unmarshal(body, &bg); err != nil {
				t.Fatal(err)
			}
			got, _ := bg["textGlow"].(string)
			if got != tc.want || len(pack.Warnings) != tc.warnings {
				t.Fatalf("background=%s warnings=%v, want textGlow=%q and %d warnings", body, pack.Warnings, tc.want, tc.warnings)
			}
			if tc.warnings > 0 && !strings.Contains(pack.Warnings[0], "textGlow") {
				t.Fatalf("warning does not identify textGlow: %v", pack.Warnings)
			}
		})
	}
}

func TestTextGlowWithoutBackgroundStaysAbsent(t *testing.T) {
	pack, err := decode([]byte(minimal), "glow-probe")
	if err != nil || pack.Background != nil || len(pack.Warnings) != 0 {
		t.Fatalf("pack=%+v err=%v", pack, err)
	}
}

func TestTextGlowWrongTypeRefusesManifest(t *testing.T) {
	for _, value := range []string{`false`, `7`, `{}`, `[]`} {
		raw := strings.TrimSuffix(minimal, "}") + `,"background":{"textGlow":` + value + `}}`
		if _, err := decode([]byte(raw), "glow-probe"); err == nil {
			t.Fatalf("accepted non-string textGlow %s", value)
		}
	}
}
