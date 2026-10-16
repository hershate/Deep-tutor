package tutor

// NormalizeQuizType maps model output onto the graded vocabulary, Deep-tutor's
// _quiz_type_safe: a value outside the enum falls back to choice, not an
// error. The server grades choice only; every other type is self-assessed.
func NormalizeQuizType(t string) QuizType {
	switch QuizType(t) {
	case QuizChoice, QuizFill, QuizShort, QuizEssay:
		return QuizType(t)
	default:
		return QuizChoice
	}
}

// GradeChoice grades a choice submission against the stored answer. A nil
// result means unknown: the quiz carries no answer (degraded generation), and
// treating that as either correct or wrong would let empty submissions move
// mastery.
func GradeChoice(answer, selected string) *bool {
	if answer == "" {
		return nil
	}
	correct := selected == answer
	return &correct
}
