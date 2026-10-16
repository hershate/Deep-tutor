package main

import "reasonix/internal/session/control"

func prepareRunTask(cfg suiteConfig, t task) (task, result, error) {
	r := result{task: t, Profile: cfg.profile, CacheArm: cfg.cacheArm, Effort: cfg.effort, ReplyLanguage: cfg.replyLanguage}
	r.Arm = cfg.arm.Arm()
	r.Anchor = cfg.anchor
	t.Prompt = anchorPrompt(cfg.anchor, t)
	if cfg.forcePlanner {
		// The host's own routing marker, so the two-model turn engages without
		// putting an English sentence in front of every prompt.
		t.Prompt = control.PlannerRouteMarker + "\n\n" + t.Prompt
		r.PlanForced = true
	}
	if cfg.trajDir != "" {
		source, err := newSFTProvenance(t)
		if err != nil {
			return t, r, err
		}
		r.SFT = source
	}

	return t, r, nil
}
