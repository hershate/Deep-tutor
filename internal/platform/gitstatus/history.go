package gitstatus

import (
	"bytes"
	"context"
	"strconv"
	"strings"
	"unicode"

	"reasonix/internal/platform/gitcmd"
)

// Commit is one commit of the history graph. Parents name the commits it
// descends from, which is all a reader needs to draw the lanes.
type Commit struct {
	ID      string   `json:"id"`
	Parents []string `json:"parents"`
	Author  string   `json:"author"`
	// At is the author time, unix seconds.
	At int64 `json:"at"`
	// Refs are the branches and tags that point here, as git decorates them.
	Refs    []string `json:"refs"`
	Subject string   `json:"subject"`
}

// HistoryPage is the newest commits of every branch, newest first, and whether
// the history goes on past them.
type HistoryPage struct {
	Commits   []Commit `json:"commits"`
	Truncated bool     `json:"truncated"`
}

const (
	// maxHistory bounds a page however much a caller asks for.
	maxHistory = 300
	// maxSubject bounds a subject, which is the commit author's text.
	maxSubject = 160
)

// History reads the repository's history across all refs in date order, newest
// first, at most limit commits (capped at maxHistory). ok is false for a
// workspace that is not version controlled, as for Summary; a repository with no
// commit yet is an empty page. Nothing is written and no hook, textconv or
// external diff driver runs: the invocation is the hardened one every read uses.
func History(ctx context.Context, repo gitcmd.Repo, limit int) (HistoryPage, bool, error) {
	if !repo.Valid() {
		return HistoryPage{}, false, nil
	}
	limit = min(max(limit, 1), maxHistory)
	// One more than asked: its presence is what "truncated" means.
	raw, err := repo.Top().Command(ctx, "log", "--all", "--date-order", "--no-color",
		"-n", strconv.Itoa(limit+1), "--format=%H%x00%P%x00%an%x00%at%x00%D%x00%s", "--").Output()
	if err != nil {
		// A repository with no commit has no log to read; that is a quiet page,
		// told from a failing git by there being no HEAD to resolve.
		if repo.Top().Command(ctx, "rev-parse", "--verify", "--quiet", "HEAD").Run() != nil {
			return HistoryPage{Commits: []Commit{}}, true, nil
		}
		return HistoryPage{}, false, err
	}
	page := HistoryPage{Commits: []Commit{}}
	for line := range bytes.SplitSeq(raw, []byte{'\n'}) {
		fields := bytes.SplitN(line, []byte{0}, 6)
		if len(fields) < 6 || len(fields[0]) == 0 {
			continue
		}
		at, _ := strconv.ParseInt(string(fields[3]), 10, 64)
		page.Commits = append(page.Commits, Commit{
			ID: string(fields[0]), Parents: strings.Fields(string(fields[1])), Author: clean(string(fields[2]), maxSubject),
			At: at, Refs: refNames(string(fields[4])), Subject: clean(string(fields[5]), maxSubject),
		})
	}
	if len(page.Commits) > limit {
		page.Commits, page.Truncated = page.Commits[:limit], true
	}
	return page, true, nil
}

// refNames splits git's decoration ("HEAD -> main, tag: v1, origin/main").
func refNames(decoration string) []string {
	out := []string{}
	for part := range strings.SplitSeq(decoration, ",") {
		if name := strings.TrimSpace(part); name != "" {
			out = append(out, clean(name, maxSubject))
		}
	}
	return out
}

// clean makes a commit author's text safe to hand on: control characters are
// dropped and the length bounded.
func clean(s string, max int) string {
	s = strings.Map(func(r rune) rune {
		if unicode.IsControl(r) {
			return -1
		}
		return r
	}, s)
	if r := []rune(s); len(r) > max {
		return string(r[:max-1]) + "…"
	}
	return s
}
