package tutor

import (
	"math"
	"time"
)

// ComputeMastery folds one update into a node's mastery (0-1). A quiz score
// caps the result from below by the score itself: a perfect quiz reaches 1.0,
// otherwise recent accuracy and history blend 70/30. Marking done sets mastery
// to 1.0 without lowering it; learning leaves it unchanged.
func ComputeMastery(old float64, status ProgressStatus, quizScore *float64) float64 {
	old = clamp01(old)
	if quizScore != nil {
		score := clamp01(*quizScore)
		return math.Round(math.Max(score, 0.7*score+0.3*old)*1000) / 1000
	}
	if status == ProgressDone {
		return math.Max(old, 1.0)
	}
	return old
}

// NextReviewDate is the spaced-review interval: lower mastery reviews sooner.
func NextReviewDate(mastery float64, today time.Time) time.Time {
	m := clamp01(mastery)
	var days int
	switch {
	case m < 0.5:
		days = 1
	case m < 0.7:
		days = 3
	case m < 0.85:
		days = 7
	default:
		days = 14
	}
	return today.AddDate(0, 0, days)
}

func clamp01(v float64) float64 {
	return math.Max(0, math.Min(1, v))
}
