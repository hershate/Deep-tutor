package builtin

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"reasonix/internal/contract/tool"
	"reasonix/internal/platform/computer"
	"reasonix/internal/safety/permission"
)

func init() {
	tool.RegisterBuiltin(computerRead{})
	tool.RegisterBuiltin(computerAct{})
}

const computerSnapshotLines = 300

// ComputerTools binds the computer-use tools to the host's CUA Driver.
func ComputerTools(session *computer.Session) []tool.Tool {
	return []tool.Tool{computerRead{session: session}, computerAct{session: session}}
}

// ComputerBound reports whether t is a computer-use tool with a driver behind it.
func ComputerBound(t tool.Tool) bool {
	switch c := t.(type) {
	case computerRead:
		return c.session != nil
	case computerAct:
		return c.session != nil
	}
	return false
}

// computerAppsSubject is the subject of listing every application, which is a
// question of its own rather than one any application's grant answers.
const computerAppsSubject = "apps"

var errNoComputer = &computer.Failure{Code: computer.CodeUnavailable, Detail: "computer use requires Reasonix Studio with its pinned CUA Driver"}

type computerRead struct{ session *computer.Session }

func (computerRead) Name() string { return "computer_read" }

func (computerRead) Description() string {
	return "See an approved application listed by what=apps. Installed entries without a targetable window provide an exact launch identity for a separate computer_act launch step. Select window by id when it has multiple windows. snapshot returns session-bound element refs in brackets for computer_act. screenshot captures that exact window; computer_act x/y use pixels of this latest displayed image. Old tokens and captures expire on refresh or reconnect."
}

func (computerRead) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{"window":{"type":"integer","minimum":1},"what":{"type":"string","enum":["apps","snapshot","screenshot"]},"app":{"type":"string"}},"required":["what"]}`)
}

func (computerRead) ReadOnly() bool                                   { return false }
func (computerRead) Sequential(context.Context, json.RawMessage) bool { return true }

// PermissionArgs names the application read, or the listing.
func (c computerRead) PermissionArgs(_ context.Context, args json.RawMessage) json.RawMessage {
	var p struct {
		Window int    `json:"window"`
		What   string `json:"what"`
		App    string `json:"app"`
	}
	_ = json.Unmarshal(args, &p)
	if p.What == "apps" {
		p.App = computerAppsSubject
	}
	return hostSubjectArgs(c.Schema(), args, "app", strings.TrimSpace(p.App))
}

func (c computerRead) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	text, _, err := c.ExecuteWithImages(ctx, args)
	return text, err
}

func (c computerRead) ExecuteWithImages(ctx context.Context, args json.RawMessage) (string, []string, error) {
	var p struct {
		Window int    `json:"window"`
		What   string `json:"what"`
		App    string `json:"app"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return "", nil, fmt.Errorf("invalid args: %w", err)
	}
	if c.session == nil {
		return "", nil, errNoComputer
	}
	switch p.What {
	case "apps":
		apps, err := c.session.Apps(ctx)
		if err != nil {
			return "", nil, err
		}
		return renderApps(apps), nil, nil
	case "snapshot":
		snap, err := c.session.Snapshot(ctx, p.App, p.Window)
		if err != nil {
			return "", nil, err
		}
		return renderComputerSnapshot(snap), nil, nil
	case "screenshot":
		shot, app, err := c.session.Screenshot(ctx, p.App, p.Window)
		if err != nil {
			return "", nil, err
		}
		return screenshotCaption(app), []string{shot}, nil
	}
	return "", nil, &computer.Failure{Code: computer.CodeBadStep, Detail: fmt.Sprintf("what=%q is not one of apps, snapshot, screenshot", p.What)}
}

func screenshotCaption(app computer.App) string {
	return fmt.Sprintf("%s (%s): front window\n[image: screenshot]", computer.ShownName(app.Name), computer.ShownBundle(app.Bundle))
}

type computerAct struct{ session *computer.Session }

func (computerAct) Name() string { return "computer_act" }

func (computerAct) Description() string {
	return "Operate an approved application through CUA Driver. launch must be one separate step using an exact installed identity from computer_read what=apps; no window selector or extra arguments. Select window when the app has multiple windows. Use refs from its latest snapshot or coordinates from its latest screenshot. Background clicks, set_value, type/paste, key and scroll never silently escalate. pointer_click explicitly takes foreground control and requires separate approval. wait is bounded. Inspect fresh state after an unverified effect; never assume an action landed. Returns a snapshot afterwards except launch, which returns the verified running identity for a separately approved read."

}

func (computerAct) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{"app":{"type":"string"},"window":{"type":"integer","minimum":1},"steps":{"type":"array","maxItems":64,"items":{"type":"object","properties":{"action":{"type":"string","enum":["launch","click","right_click","set_value","type","paste","key","scroll","wait","pointer_click"]},"ref":{"type":"string"},"text":{"type":"string"},"key":{"type":"string"},"x":{"type":"number"},"y":{"type":"number"},"amount":{"type":"number"},"ms":{"type":"integer"},"to_x":{"type":"number"},"to_y":{"type":"number"},"button":{"type":"string","enum":["left","right","middle"]},"times":{"type":"integer"},"seconds":{"type":"number"}},"required":["action"]}}},"required":["app","steps"]}`)
}

func (computerAct) ReadOnly() bool                                   { return false }
func (computerAct) Sequential(context.Context, json.RawMessage) bool { return true }

// PermissionArgs names the application operated.
func (c computerAct) PermissionArgs(_ context.Context, args json.RawMessage) json.RawMessage {
	var p struct {
		App    string          `json:"app"`
		Window int             `json:"window"`
		Steps  []computer.Step `json:"steps"`
	}
	_ = json.Unmarshal(args, &p)
	app := strings.TrimSpace(p.App)
	if app != "" && computer.PointerSteps(p.Steps) {
		app = permission.ComputerPointerPrefix + app
	}
	return hostSubjectArgs(c.Schema(), args, "app", app)
}

func (c computerAct) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	var p struct {
		App    string          `json:"app"`
		Window int             `json:"window"`
		Steps  []computer.Step `json:"steps"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return "", fmt.Errorf("invalid args: %w", err)
	}
	if c.session == nil {
		return "", errNoComputer
	}
	if len(p.Steps) == 0 {
		return "", &computer.Failure{Code: computer.CodeBadStep, Detail: "steps is empty"}
	}
	res, stepErr := c.session.Act(ctx, p.App, p.Steps, p.Window)
	var out strings.Builder
	fmt.Fprintf(&out, "Completed %d of %d step(s).\n", res.Done, len(p.Steps))
	for i, step := range res.Steps {
		fmt.Fprintf(&out, "  %d. %s%s\n", i+1, step.Note, renderEffect(step.Effect))
	}
	if stepErr != nil {
		fmt.Fprintf(&out, "Step %d failed: %v\n", res.FailedAt+1, stepErr)
	}
	if res.App.Bundle == "" {
		return out.String(), stepErr
	}
	if len(p.Steps) == 1 && p.Steps[0].Action == "launch" {
		out.WriteString("Launched application identity (read separately under its own approval):\n" + renderApps([]computer.App{res.App}))
		return out.String(), stepErr
	}
	snap, err := c.session.Snapshot(ctx, res.App.Bundle, p.Window)
	if err != nil {
		fmt.Fprintf(&out, "The application could not be read afterwards: %v\n", err)
		return out.String(), stepErr
	}
	out.WriteString("Now:\n" + renderComputerSnapshot(snap))
	return out.String(), stepErr
}

func renderApps(apps []computer.App) string {
	var b strings.Builder
	for _, app := range apps {
		mark := " "
		if app.Active {
			mark = "*"
		}
		fmt.Fprintf(&b, "%s %s — %s", mark, computer.ShownBundle(app.Bundle), computer.ShownName(app.Name))
		if why := computer.Refused(app.Bundle); why != "" {
			fmt.Fprintf(&b, " (never operated: %s)", why)
		}
		if app.PID == 0 && len(app.Windows) == 0 {
			if app.Launchable {
				b.WriteString(" (installed; no targetable window; launch separately)")
			} else {
				b.WriteString(" (installed; no targetable window; launch unsupported)")
			}
		}
		b.WriteString("\n")
		for _, w := range app.Windows {
			title := `"(untitled)"`
			if w.Title != "" {
				title = computer.ShownName(w.Title)
			}
			fmt.Fprintf(&b, "    window %d %s %.0f×%.0f\n", w.ID, title, w.Bounds.Width, w.Bounds.Height)
		}
	}
	if b.Len() == 0 {
		return "No applications are running with windows a person can see.\n"
	}
	return b.String()
}

// effectSaid and evidenceSaid are how a step's effect reads to the model.
var effectSaid = map[computer.EffectClass]string{
	computer.EffectConfirmed:     "took effect",
	computer.EffectSuspectedNoop: "no effect seen",
	computer.EffectUnverifiable:  "effect not verified",
}

var evidenceSaid = map[computer.Evidence]string{
	computer.EvidenceValueReadback:  "value read back",
	computer.EvidenceValueUnchanged: "value unchanged",
}

func renderEffect(e computer.Effect) string {
	if e.Class == "" {
		return ""
	}
	var b strings.Builder
	b.WriteString(" — " + effectSaid[e.Class])
	for _, ev := range e.Evidence {
		b.WriteString(" (" + evidenceSaid[ev] + ")")
	}
	if e.BlockedBy != nil {
		b.WriteString(", into " + e.BlockedBy.String())
	}
	if e.Driver != nil {
		fmt.Fprintf(&b, " [CUA effect=%s route=%s]", e.Driver.Effect, e.Driver.Route)
		if e.Driver.Delivery != nil && e.Driver.Delivery.Count != nil {
			fmt.Fprintf(&b, " delivered=%d", *e.Driver.Delivery.Count)
		}
	}
	return b.String()
}

func renderComputerSnapshot(snap computer.Snapshot) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s (%s)\n", computer.ShownName(snap.App.Name), computer.ShownBundle(snap.App.Bundle))
	for _, m := range snap.Modals {
		fmt.Fprintf(&b, "Blocked: %s holds this application's input; the window behind it takes none until it is answered.\n", m)
	}
	if snap.Note != "" {
		b.WriteString(snap.Note + "\n")
	}
	lines := snap.Lines
	if len(lines) > computerSnapshotLines {
		lines = lines[:computerSnapshotLines]
	}
	for _, l := range lines {
		b.WriteString(l + "\n")
	}
	if snap.Truncated || len(snap.Lines) > len(lines) {
		b.WriteString("… the tree is longer than this; act on what is shown or take a screenshot\n")
	}
	return b.String()
}
