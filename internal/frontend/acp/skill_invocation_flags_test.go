package acp

import (
	"testing"

	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/ext/skill"
	"deep-tutor/internal/session/control"
)

func TestAvailableCommandsHonourUserInvocableAndArgumentHint(t *testing.T) {
	ctrl := control.New(control.Options{Sink: event.Discard, Skills: []skill.Skill{
		{Name: "deploy", Scope: skill.ScopeProject, InvocationFlags: skill.InvocationFlags{ArgumentHint: "[env]"}},
		{Name: "ctx-notes", Scope: skill.ScopeProject, InvocationFlags: skill.InvocationFlags{DisableUserInvocation: true}},
	}})
	defer ctrl.Close()

	var deploy *AvailableCommand
	cmds := availableCommandsFor(ctrl)
	for i := range cmds {
		if cmds[i].Name == "ctx-notes" {
			t.Fatal("ACP offers a user-invocable:false skill")
		}
		if cmds[i].Name == "deploy" {
			deploy = &cmds[i]
		}
	}
	if deploy == nil || deploy.Input == nil || deploy.Input.Hint != "[env]" {
		t.Fatalf("deploy = %+v, want input hint [env]", deploy)
	}
}
