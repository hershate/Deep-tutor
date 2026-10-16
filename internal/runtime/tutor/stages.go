package tutor

import (
	"errors"
	"fmt"
)

// ErrStageCount reports a design_stages output outside the 3-6 contract. The
// pipeline feeds it back as repair feedback instead of degrading.
var ErrStageCount = errors.New("tutor: stage count out of range")

// ValidateStages enforces the 3-6 stage contract.
func ValidateStages(stages []Stage) error {
	if len(stages) < 3 || len(stages) > 6 {
		return fmt.Errorf("%w: got %d, want 3-6", ErrStageCount, len(stages))
	}
	return nil
}

// NormalizeStages clamps stage difficulty into 1-5; out-of-range model output
// must not leak into difficulty-step validation.
func NormalizeStages(stages []Stage) {
	for i := range stages {
		stages[i].Difficulty = max(1, min(5, stages[i].Difficulty))
	}
}

// DefaultStages is design_stages' degraded default: a generic three-stage
// ladder that keeps delivery alive without a model call.
func DefaultStages() []Stage {
	return []Stage{
		{Index: 0, Name: "入门", Goal: "建立基础", Difficulty: 1, EstimatedWeeks: 1, Milestone: "理解基础概念"},
		{Index: 1, Name: "进阶", Goal: "深入核心", Difficulty: 3, EstimatedWeeks: 2, Milestone: "完成核心练习"},
		{Index: 2, Name: "实战", Goal: "综合应用", Difficulty: 5, EstimatedWeeks: 2, Milestone: "完成实战项目"},
	}
}
