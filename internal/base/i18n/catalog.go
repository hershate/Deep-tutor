package i18n

// M is the active catalogue. DetectLanguage replaces it; English is the
// default so any code path that runs before detection still has text.
var (
	M               = English
	currentLanguage = "en"
)

// Catalog returns the messages for a tag without installing them. The desktop
// shell needs exactly this: its interface language is a setting of its own,
// separate from the kernel's, and one process holds both — so it must read a
// catalogue rather than replace the active one.
func Catalog(tag string) Messages {
	switch normalize(tag) {
	case "zh":
		return Chinese
	case "zh-TW":
		return ChineseTraditional
	case "en":
		return English
	}
	return M
}

// CatalogFor resolves an interface-language preference the way DetectLanguage
// does — the setting, then the environment, then the machine — and installs
// nothing. Catalog alone cannot: an empty preference means "follow the
// machine", and what it returned instead was whatever the process had already
// installed, which for a window that never installs anything is English.
func CatalogFor(pref string) Messages {
	for _, candidate := range append([]string{pref}, envCandidates()...) {
		if tag := normalize(candidate); tag != "" {
			return Catalog(tag)
		}
	}
	return English
}

// CurrentLanguage returns the language tag installed by the latest
// DetectLanguage call. It lets frontends reuse the resolved locale without
// re-reading the environment and accidentally ignoring an explicit override.
func CurrentLanguage() string {
	return currentLanguage
}
