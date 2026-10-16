package computer

import (
	"context"
	"os"
	"path/filepath"
	"strings"
)

type launchSpecification struct {
	identity   string
	executable string
	args       map[string]any
}

func catalogueLaunchSpecification(app nativeApplication) (launchSpecification, error) {
	if why := Refused(app.Bundle); why != "" {
		return launchSpecification{}, fail(CodeAppRefused, "%s is %s", app.Bundle, why)
	}
	spec, err := nativeLaunchSpecification(app)
	if err != nil {
		return spec, err
	}
	if why := Refused(filepath.Base(spec.executable)); spec.executable != "" && why != "" {
		return spec, fail(CodeAppRefused, "%s is %s", spec.identity, why)
	}
	return spec, nil
}

func applicationLaunch(app nativeApplication) (launchSpecification, error) {
	spec, err := catalogueLaunchSpecification(app)
	if err != nil {
		return spec, err
	}
	if spec.executable != "" {
		resolved, err := filepath.EvalSymlinks(spec.executable)
		if err != nil {
			return spec, fail(CodeUnsupported, "catalogue executable cannot be resolved")
		}
		if why := Refused(filepath.Base(resolved)); why != "" {
			return spec, fail(CodeAppRefused, "%s is %s", spec.identity, why)
		}
		file, err := os.Stat(resolved)
		if err != nil || !file.Mode().IsRegular() {
			return spec, fail(CodeUnsupported, "catalogue launch target is not an existing executable file")
		}
		if err := attestNativeLaunchBinary(resolved); err != nil {
			return spec, err
		}
		spec.executable = resolved
		for field := range spec.args {
			spec.args[field] = resolved
		}
	}
	return spec, nil
}

func (s *Session) launchCandidate(ctx context.Context, identity string) (launchSpecification, error) {
	catalogue, err := s.catalogue(ctx)
	if err != nil {
		return launchSpecification{}, err
	}
	var found []nativeApplication
	for _, app := range catalogue {
		spec, err := catalogueLaunchSpecification(app)
		if err != nil {
			if app.Bundle == identity {
				return spec, err
			}
			continue
		}
		if spec.identity == identity {
			found = append(found, app)
		}
	}
	if len(found) != 1 {
		return launchSpecification{}, fail(CodeNoApp, "launch identity %q must resolve to exactly one native catalogue entry", identity)
	}
	return applicationLaunch(found[0])
}

func (s *Session) launch(ctx context.Context, identity string, steps []Step, windows []int) (ActResult, error) {
	res := ActResult{FailedAt: 0}
	if len(steps) != 1 || steps[0] != (Step{Action: "launch"}) {
		return res, fail(CodeBadStep, "launch must be a single separate step without input arguments")
	}
	for _, id := range windows {
		if id != 0 {
			return res, fail(CodeBadStep, "launch does not accept a window selector")
		}
	}
	spec, err := s.launchCandidate(ctx, strings.TrimSpace(identity))
	if err != nil {
		return res, err
	}
	s.state = observation{}
	var launched struct {
		PID    int32  `json:"pid"`
		Bundle string `json:"bundle_id"`
	}
	if _, err := s.read(ctx, "launch_app", spec.args, &launched); err != nil {
		return res, err
	}
	res.Done = 1
	res.Steps = []StepResult{{Note: "launch request delivered", Effect: Effect{Class: EffectUnverifiable}}}
	if launched.PID <= 0 {
		return res, fail(CodeEffectUnknown, "launch returned no target process; inspect application discovery before retrying")
	}
	if spec.executable != "" {
		actual, err := processExecutable(launched.PID)
		matches := sameExecutablePath(actual, spec.executable)
		if err != nil || !matches {
			return res, fail(CodeEffectUnknown, "launched process does not attest the exact catalogue executable")
		}
	} else if launched.Bundle != spec.identity {
		return res, fail(CodeEffectUnknown, "launched process does not attest the catalogue bundle")
	}
	bundle, exe, err := processIdentity(launched.PID, launched.Bundle)
	if err != nil {
		return res, err
	}
	if why := Refused(exe); why != "" {
		return res, fail(CodeAppRefused, "launched process is %s", why)
	}
	app, err := s.appForPID(ctx, bundle, launched.PID)
	if err != nil || app.PID != launched.PID {
		return res, fail(CodeEffectUnknown, "launched application has not produced a unique targetable process; inspect fresh state")
	}
	res.App, res.FailedAt = app, -1
	return res, nil
}
