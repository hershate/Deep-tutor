package tutor

import (
	"embed"
	"fmt"
)

//go:embed skills/*.md
var skillFS embed.FS

// SkillPrompt returns the methodology body for a pipeline tool. The bodies
// are ported verbatim from Deep-tutor's SKILL.md assets and serve as the
// system prompt for the matching LLM tool.
func SkillPrompt(name string) (string, error) {
	data, err := skillFS.ReadFile("skills/" + name + ".md")
	if err != nil {
		return "", fmt.Errorf("tutor: unknown skill %q: %w", name, err)
	}
	return string(data), nil
}
