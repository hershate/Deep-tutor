package main

import (
	"testing"

	"deep-tutor/internal/base/i18n"
	"deep-tutor/internal/contract/config"
)

func TestKernelLanguageWithShellSystemLanguage(t *testing.T) {
	before := i18n.CurrentLanguage()
	t.Cleanup(func() { i18n.DetectLanguage(before) })
	for _, key := range []string{"REASONIX_LANG", "LC_ALL", "LC_MESSAGES", "LANG"} {
		t.Setenv(key, "")
	}
	t.Setenv("REASONIX_SYSTEM_LANG", "zh-CN")
	for _, tt := range []struct{ setting, env, want string }{
		{"auto", "", "zh"},
		{"en", "", "en"},
		{"auto", "en", "en"},
		{"zh-TW", "en", "zh-TW"},
	} {
		t.Setenv("REASONIX_LANG", tt.env)
		if got := resolveKernelLanguage(&config.Config{Language: tt.setting}); got != tt.want {
			t.Errorf("setting %q, env %q: language = %q, want %q", tt.setting, tt.env, got, tt.want)
		}
	}
}
