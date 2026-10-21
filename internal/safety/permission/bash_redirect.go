package permission

import "deep-tutor/internal/safety/shellsafe"

func normalizeBashSafeRedirectsForMatch(subject string) (string, bool) {
	return shellsafe.NormalizeBashSafeRedirectsForMatch(subject)
}
