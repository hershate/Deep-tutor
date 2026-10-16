package computer

import (
	"context"
	"path/filepath"
	"strings"
)

type nativeWindow struct {
	ID     int    `json:"window_id"`
	PID    int32  `json:"pid"`
	Title  string `json:"title"`
	Bounds Rect   `json:"bounds"`
}

type nativeApplication struct {
	PID        int32  `json:"pid"`
	Bundle     string `json:"bundle_id"`
	Name       string `json:"name"`
	Active     bool   `json:"active"`
	Running    bool   `json:"running"`
	LaunchPath string `json:"launch_path"`
}

func (s *Session) Apps(ctx context.Context) ([]App, error) {
	s.driver.desktop.Lock()
	defer s.driver.desktop.Unlock()
	catalogue, err := s.catalogue(ctx)
	if err != nil {
		return nil, err
	}
	apps := runningApplications(catalogue)
	if len(apps) > 0 {
		windows, err := s.windows(ctx, 0)
		if err != nil {
			return nil, err
		}
		for i := range apps {
			attachWindows(&apps[i], windows)
		}
	}
	for _, a := range catalogue {
		if a.Running || a.Bundle == "" {
			continue
		}
		installed := App{Bundle: a.Bundle, Name: a.Name}
		if spec, err := catalogueLaunchSpecification(a); err == nil {
			installed.Bundle, installed.Launchable = spec.identity, true
		}
		apps = append(apps, installed)
	}
	return apps, nil
}

func (s *Session) catalogue(ctx context.Context) ([]nativeApplication, error) {
	var response struct {
		Apps []nativeApplication `json:"apps"`
	}
	if _, err := s.read(ctx, "list_apps", map[string]any{}, &response); err != nil {
		return nil, err
	}
	return response.Apps, nil
}

func (s *Session) runningApps(ctx context.Context) ([]App, error) {
	catalogue, err := s.catalogue(ctx)
	if err != nil {
		return nil, err
	}
	return runningApplications(catalogue), nil
}

func runningApplications(catalogue []nativeApplication) []App {
	var apps []App
	for _, a := range catalogue {
		if !a.Running || a.PID <= 0 {
			continue
		}
		bundle, exe, err := processIdentity(a.PID, a.Bundle)
		if err != nil {
			continue
		}
		apps = append(apps, App{PID: a.PID, Bundle: bundle, Exe: exe, Name: a.Name, Active: a.Active})
	}
	return apps
}

func (s *Session) windows(ctx context.Context, pid int32) ([]nativeWindow, error) {
	args := map[string]any{}
	if pid > 0 {
		args["pid"] = pid
	}
	var response struct {
		Windows []nativeWindow `json:"windows"`
	}
	if _, err := s.read(ctx, "list_windows", args, &response); err != nil {
		return nil, err
	}
	return response.Windows, nil
}

func attachWindows(app *App, windows []nativeWindow) {
	for _, w := range windows {
		if w.PID == app.PID && w.ID > 0 {
			app.Windows = append(app.Windows, Window{ID: w.ID, Title: w.Title, Bounds: w.Bounds})
		}
	}
}

func (s *Session) app(ctx context.Context, bundle string) (App, error) {
	return s.appForPID(ctx, bundle, 0)
}

func (s *Session) appForPID(ctx context.Context, bundle string, expectedPID int32) (App, error) {
	bundle = strings.TrimSpace(bundle)
	if why := Refused(bundle); why != "" {
		return App{}, fail(CodeAppRefused, "%s is %s", bundle, why)
	}
	apps, err := s.runningApps(ctx)
	if err != nil {
		return App{}, err
	}
	var found []App
	for _, app := range apps {
		if app.Bundle == bundle {
			if why := Refused(app.Exe); why != "" {
				return App{}, fail(CodeAppRefused, "%s is %s", bundle, why)
			}
			found = append(found, app)
		}
	}
	if len(found) == 0 {
		installed, err := s.installedIdentity(ctx, bundle)
		if err != nil {
			return App{}, err
		}
		if installed {
			return App{}, fail(CodeNoWindow, "application is installed but has no targetable window; inspect what=apps for an explicit launch or manual recovery")
		}
		return App{}, fail(CodeNoApp, "no running application has identity %q; use the exact identity returned by computer_read what=apps", bundle)
	}
	if len(found) > 1 {
		return App{}, fail(CodeNoApp, "application identity %q resolves to multiple processes", bundle)
	}
	app := found[0]
	if expectedPID > 0 && app.PID != expectedPID {
		return App{}, fail(CodeNoApp, "application identity no longer belongs to the launched process")
	}
	windows, err := s.windows(ctx, app.PID)
	if err != nil {
		return App{}, err
	}
	attachWindows(&app, windows)
	return app, nil
}

func (s *Session) installedIdentity(ctx context.Context, identity string) (bool, error) {
	catalogue, err := s.catalogue(ctx)
	if err != nil {
		return false, err
	}
	for _, app := range catalogue {
		if app.Running {
			continue
		}
		if app.Bundle == identity {
			return true, nil
		}
		spec, err := catalogueLaunchSpecification(app)
		if err == nil && (spec.identity == identity || (spec.executable != "" && filepath.Base(spec.executable) == identity)) {
			return true, nil
		}
	}
	return false, nil
}
