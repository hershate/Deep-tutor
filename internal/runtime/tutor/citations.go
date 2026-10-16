package tutor

import (
	"regexp"
	"strings"
)

// citations.go is the hard citation check: a unit's markdown may only link
// to URLs from the plan's verified resource pool (plus screened supplements);
// invented links are stripped, visible text stays.

// normalizePoolURL matches screenURLKey's rules so pool membership and gate
// verdicts agree on identity.
func normalizePoolURL(raw string) string {
	return screenURLKey(raw)
}

// CollectMaterialURLs gathers the citable pool: plan resources and unit
// supplements that passed verification (ok or uncertain — never invalid or
// unlabeled).
func CollectMaterialURLs(plan *Plan, supplements []Resource) map[string]bool {
	pool := map[string]bool{}
	keep := func(rs []Resource) {
		for _, r := range rs {
			if (r.Verified == VerifyOK || r.Verified == VerifyUncertain) && r.URL != "" {
				pool[normalizePoolURL(r.URL)] = true
			}
		}
	}
	for _, st := range plan.Stages {
		keep(st.Resources)
	}
	keep(supplements)
	return pool
}

var (
	autolinkRe = regexp.MustCompile(`<(https?://[^>\s]+)>`)
	linkDefRe  = regexp.MustCompile(`^(\[[^\]]+\]:\s*)(https?://\S+)`)
	bareURLRe  = regexp.MustCompile(`https?://[^\s<>\])"']+`)
	listItemRe = regexp.MustCompile(`^\s*(?:[-*+]|\d+\.)\s`)
	mdLink     = regexp.MustCompile(`\[([^\]]*)\]\((https?://[^)\s"']+)(?:\s+"[^"]*")?\)`)
)

// SanitizeCitations rewrites content so only whitelisted URLs remain
// clickable: standard links keep or lose their brackets, autolinks and bare
// URLs in prose get neutralized, link definitions outside the pool drop
// their lines, and list items whose every link is foreign drop entirely.
// Code spans and fenced blocks are untouched — they never render as links.
func SanitizeCitations(content string, pool map[string]bool) string {
	if content == "" {
		return content
	}
	allowed := func(raw string) bool { return pool[normalizePoolURL(raw)] }

	var out []string
	inFence := false
	for line := range strings.SplitSeq(content, "\n") {
		if strings.HasPrefix(strings.TrimSpace(line), "```") || strings.HasPrefix(strings.TrimSpace(line), "~~~") {
			inFence = !inFence
			out = append(out, line)
			continue
		}
		if inFence {
			out = append(out, line)
			continue
		}
		if def := linkDefRe.FindStringSubmatch(line); def != nil && !allowed(def[2]) {
			continue // a live definition would revive the link downstream
		}
		line = neutralizeAutolinks(line, allowed)
		line, keep := rewriteStandardLinks(line, allowed)
		if !keep {
			continue
		}
		line = rewriteBareURLs(line, allowed)
		out = append(out, line)
	}
	joined := strings.Join(out, "\n")
	if strings.HasSuffix(content, "\n") && !strings.HasSuffix(joined, "\n") {
		joined += "\n"
	}
	return joined
}

// neutralizeAutolinks removes <url> forms the pool does not cover.
func neutralizeAutolinks(line string, allowed func(string) bool) string {
	return autolinkRe.ReplaceAllStringFunc(line, func(m string) string {
		raw := strings.TrimSuffix(strings.TrimPrefix(m, "<"), ">")
		if allowed(raw) {
			return m
		}
		return ""
	})
}

// rewriteStandardLinks handles [text](url): whitelisted stays, foreign
// becomes its bare text. A list item whose links are all foreign loses the
// whole line — a citation row with nothing left to cite.
func rewriteStandardLinks(line string, allowed func(string) bool) (string, bool) {
	matches := mdLink.FindAllStringSubmatch(line, -1)
	if len(matches) == 0 {
		return line, true
	}
	anyAllowed := false
	for _, m := range matches {
		if allowed(m[2]) {
			anyAllowed = true
			break
		}
	}
	if !anyAllowed && listItemRe.MatchString(line) {
		return "", false
	}
	return mdLink.ReplaceAllStringFunc(line, func(m string) string {
		sub := mdLink.FindStringSubmatch(m)
		if allowed(sub[2]) {
			return m
		}
		return sub[1]
	}), true
}

// rewriteBareURLs converts foreign bare URLs to inline code so nothing
// auto-links them; the address itself stays visible. Standard links were
// already consumed, and inline code spans are skipped.
func rewriteBareURLs(line string, allowed func(string) bool) string {
	parts := strings.Split(line, "`")
	var rebuilt []string
	for i, part := range parts {
		if i%2 == 1 { // inside a code span
			rebuilt = append(rebuilt, part)
			continue
		}
		part = bareURLRe.ReplaceAllStringFunc(part, func(m string) string {
			if allowed(m) {
				return m
			}
			// A short prefix of an already-consumed standard link can match
			// here; only neutralize when it is not part of one.
			return "`" + m + "`"
		})
		rebuilt = append(rebuilt, part)
	}
	return strings.Join(rebuilt, "`")
}
