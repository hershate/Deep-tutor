package main

import (
	"fmt"
	"sort"
	"strings"
)

// runTrials runs one task k times and keeps every outcome, where attempts stops
// at the first pass. The two answer different questions: Pass@≤N is whether
// the agent can solve a task at all, pass^k whether it does so every time.
func runTrials(cfg suiteConfig, t task, total *int) []result {
	var out []result
	for trial := 1; trial <= cfg.trials; trial++ {
		if cfg.budget > 0 && *total >= cfg.budget {
			out = append(out, result{task: t, Profile: cfg.profile, Trial: trial, Skipped: true, Note: "skipped: token budget reached"})
			continue
		}
		run := cfg
		run.trial = trial
		r := runTask(run, t)
		r.Trial = trial
		*total += r.PromptTokens + r.CompletionTokens
		out = append(out, r)
		if answerLeakDetected(r) {
			break
		}
	}
	return out
}

// trialTally is one task's trials: how many ran to a grade, how many passed.
type trialTally struct{ ran, passed int }

func tallyTrials(results []result) (map[string]trialTally, int) {
	tally := map[string]trialTally{}
	k := 0
	for _, r := range results {
		if r.Trial == 0 || r.Skipped || r.NoSolution {
			continue
		}
		t := tally[r.ID]
		t.ran++
		if r.Passed {
			t.passed++
		}
		tally[r.ID] = t
		k = max(k, r.Trial)
	}
	return tally, k
}

// trialsLine reports reliability over repeated trials. pass^k and pass@k are
// over the tasks that ran all k trials (k is the highest trial index that ran);
// a task cut short by the budget has no value for either and is listed apart.
// mean pass@1 is passes over every trial that ran to a grade. Empty for a run
// without trials, so older reports read as before.
func trialsLine(results []result) string {
	tally, k := tallyTrials(results)
	if k < 2 || len(tally) == 0 {
		return ""
	}
	all, any, complete, runs, passes := 0, 0, 0, 0, 0
	var flaky, incomplete []string
	for id, t := range tally {
		runs += t.ran
		passes += t.passed
		if t.passed > 0 && t.passed < t.ran {
			flaky = append(flaky, fmt.Sprintf("`%s` %d/%d", id, t.passed, t.ran))
		}
		if t.ran < k {
			incomplete = append(incomplete, fmt.Sprintf("`%s` %d/%d", id, t.ran, k))
			continue
		}
		complete++
		if t.passed == t.ran {
			all++
		}
		if t.passed > 0 {
			any++
		}
	}
	sort.Strings(flaky)
	sort.Strings(incomplete)
	passAll, passAny := fmt.Sprintf("n/a (no task ran %d trials)", k), "n/a"
	if complete > 0 {
		passAll, passAny = shareOf(all, complete), shareOf(any, complete)
	}
	line := fmt.Sprintf("**Reliability** (%d trials, %d/%d tasks complete): **pass^%d** %s · **pass@%d** %s · **mean pass@1** %s\n\n",
		k, complete, len(tally), k, passAll, k, passAny, share(passes, runs))
	if len(incomplete) > 0 {
		line += fmt.Sprintf("**Incomplete** (fewer than %d trials, not in pass^%d or pass@%d): %s\n\n", k, k, k, strings.Join(incomplete, " · "))
	}
	if len(flaky) > 0 {
		line += "**Flaky** (passed some trials, not all): " + strings.Join(flaky, " · ") + "\n\n"
	}
	return line
}

func shareOf(n, d int) string {
	return fmt.Sprintf("%s (%d/%d complete)", pct(n, d), n, d)
}

func share(n, d int) string {
	return fmt.Sprintf("%s (%d/%d)", pct(n, d), n, d)
}
