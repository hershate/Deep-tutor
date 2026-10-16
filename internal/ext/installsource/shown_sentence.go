package installsource

import "fmt"

func shadowedSkillWarning(name, installed, selected string) string {
	return fmt.Sprintf("skill %q installed at %s is shadowed in this workspace by %s", name, hostLiteral(installed), hostLiteral(selected))
}

func unselectedSkillWarning(name, source, selected string) string {
	return fmt.Sprintf("skill %q registered from %s is not selected in this workspace; current selection is %s", name, hostLiteral(source), hostLiteral(selected))
}
