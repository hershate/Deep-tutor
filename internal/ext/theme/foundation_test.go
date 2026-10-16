package theme

import (
	"testing"

	"reasonix/internal/base/testenv"
)

func TestFoundationPalettesAreCompleteAndReadable(t *testing.T) {
	t.Setenv("REASONIX_HOME", testenv.TempDir(t))
	for _, id := range []string{"official-clear-blue", "official-tea-green"} {
		t.Run(id, func(t *testing.T) {
			pack, err := Load(id)
			if err != nil {
				t.Fatal(err)
			}
			if pack.Background != nil || pack.Sky != nil || len(pack.Warnings) != 0 {
				t.Fatalf("foundation must be plain and valid: %+v", pack)
			}
			for _, scheme := range []string{"light", "dark"} {
				tokens := pack.Tokens[scheme]
				for _, name := range []string{"radiusXs", "radiusSm", "radiusMd", "fontUi", "fontMono"} {
					if _, ok := tokens[name]; ok {
						t.Fatalf("palette changes %s", name)
					}
				}
				for _, name := range []string{"accentInk", "link", "brand", "labelAgent"} {
					for _, surface := range []string{"bg", "bgSoft", "bgElev", "panel", "float", "floatHi", "codeBg"} {
						if got := contrastRatio(t, tokens[name], tokens[surface]); got < 4.5 {
							t.Errorf("%s %s on %s: %.2f", scheme, name, surface, got)
						}
					}
				}
				if got := contrastRatio(t, tokens["accentFg"], tokens["accent"]); got < 4.5 {
					t.Errorf("%s button text contrast: %.2f", scheme, got)
				}
			}
		})
	}
}
