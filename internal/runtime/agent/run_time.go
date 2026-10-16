package agent

import "context"

type runTimeSinkKey struct{}

// WithRunTimeSink asks every Run under ctx to report, as it ends, how long it
// ran up to its final answer. A Run that ended in error, or on no answer,
// reports nothing, so a host summing the reports never counts a failed attempt.
func WithRunTimeSink(ctx context.Context, report func(ms int64)) context.Context {
	return context.WithValue(ctx, runTimeSinkKey{}, report)
}

// WithoutRunTimeSink hands a nested run a context its host's sink cannot reach:
// reporting belongs to the top-level run alone, so a sub-agent's or planner's
// time, already inside the turn's wall clock or kept in another session, is
// never counted a second time.
func WithoutRunTimeSink(ctx context.Context) context.Context {
	return context.WithValue(ctx, runTimeSinkKey{}, (func(int64))(nil))
}

// takeRunTimeSink claims the sink for the Run that reads it. Everything that
// Run starts — a sub-agent, a reviewer, a hook's model call — inherits a
// context without one, so a nested Run is safe by default and only a host that
// installs a sink itself ever receives reports.
func takeRunTimeSink(ctx context.Context) (context.Context, func(ms int64, err error)) {
	sink, _ := ctx.Value(runTimeSinkKey{}).(func(int64))
	if sink == nil {
		return ctx, func(int64, error) {}
	}
	return WithoutRunTimeSink(ctx), func(ms int64, err error) {
		if err == nil && ms > 0 {
			sink(ms)
		}
	}
}
