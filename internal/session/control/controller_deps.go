package control

import (
	"context"
	"deep-tutor/internal/state/sessionstore"
	"sync"

	"deep-tutor/internal/contract/ablation"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
	"deep-tutor/internal/ext/extension"
	"deep-tutor/internal/ext/hook"
	"deep-tutor/internal/ext/plugin"
	"deep-tutor/internal/ext/skill"
	"deep-tutor/internal/model/billing"
	"deep-tutor/internal/platform/gitcmd"
	"deep-tutor/internal/runtime/agent"
	"deep-tutor/internal/runtime/goaleval"
	"deep-tutor/internal/runtime/guardian"
	"deep-tutor/internal/runtime/promptrefine"
	"deep-tutor/internal/runtime/recovery"
	"deep-tutor/internal/runtime/taskmonitor"
	"deep-tutor/internal/runtime/usecap"
	"deep-tutor/internal/safety/permission"
	"deep-tutor/internal/safety/sandbox"
	"deep-tutor/internal/state/workspacelease"
	"deep-tutor/internal/tools/jobs"
)

// controllerDeps is what assembly hands the controller and never rebinds. The
// split is for the reader and the lock discipline: c.turn and c.policy read
// alike, and only one can change under you. Embedded, so existing reads resolve.
type controllerDeps struct {
	runner       agent.Runner
	executor     *agent.Agent
	guardianSess *guardian.Session // nil when guardian is disabled
	// recoveryGate is the shared Auto Guard state for this controller.
	// nil when the feature is not wired for this controller.
	recoveryGate *recovery.Gate

	// taskBudget is the configured spend gate, as passed at construction.
	taskBudget agent.TaskBudget
	// goalTokenBudget bounds an unattended Goal loop; 0 leaves it unbounded.
	goalTokenBudget int
	// evaluator is the bounded Goal completion evaluator consulted when the
	// working model submits no update_goal report. nil fails closed: the goal
	// pauses instead of defaulting to continue.
	evaluator goaleval.Evaluator
	refiner   *promptrefine.Refiner
	committer CommitDrafter
	// commitMu serialises commits so two confirmations cannot interleave.
	commitMu sync.Mutex
	// goalUsageTee accounts billable usage events into the active goal turn's
	// observational token total. It wraps the public sink when the caller didn't provide one.
	goalUsageTee *goalUsageTee
	sink         event.Sink
	policy       permission.Policy
	// subagentGate is the shared gate every headless-only sub-agent surface
	// reads from (see Options.SubagentGate). Nil when the caller didn't build
	// one — sub-agents then keep whatever gate they were constructed with.
	subagentGate *SharedHeadlessGate

	// observe is non-nil for a read-only unattended run; see ObserveRun.
	observe *ObserveRun

	label               string
	modelRef            string
	effort              string
	providerFingerprint string
	modelModes          []config.ModelMode // what SetModelMode accepts; see model_modes.go
	modelFace           *ModelFace         // what the build resolved for the session's model; see model_face.go
	sessionDir          string
	// skills owns the session's discovered skills (enabled subset, full set, and
	// the reloadable stores) — the skills slice of the Capabilities concern. See
	// skill.go.
	skills              skillSet
	date                dateAnchor
	skillRunner         skill.SubagentRunner
	readOnlySkillRunner skill.SubagentRunner
	skillProfile        skill.ProfileResolver
	hooks               *hook.Runner // session hook runner; nil-safe (no hooks configured)
	// memory owns the loaded memory snapshot, the pending turn-tail notes queue,
	// and write serialization behind its own locks, off c.mu — so a memory-panel
	// save never stalls an approval or status poll. See memory.go.
	memory                 memoryManager
	cleanup                func()
	display                displayPrefs // what this session shows; see display_prefs.go
	disableColdResumePrune bool         // legacy; rewrite elision removed, still gates cold notice

	shell               sandbox.Shell                    // interpreter for user-invoked "!" commands; zero = auto
	onRemember          func(rule string) RememberResult // set via Options; invoked when user picks "always allow"
	sessionRecoveryMeta func(SessionRecoveryRequest) sessionstore.BranchMeta

	// balance is the active provider's optional wallet endpoint (nil-answering
	// when the provider declares none). Captured at build so a model/key switch —
	// which rebuilds the controller — refreshes it.
	balance *billing.Cache

	feedback FeedbackOptions

	// jobs is the session-scoped background-job manager. The agent's background
	// tools spawn into it; Compose drains its completion notes into the next turn;
	// Close cancels its still-running jobs.
	jobs      *jobs.Manager
	taskStore taskmonitor.WriteStore
	// workspaceLease is the Delivery writer owner shared with the executor.
	// It is exposed only through a sanitized state snapshot for Desktop recovery.
	workspaceLease *workspacelease.Owner

	// mcp owns the live tool/plugin surface — Host, registry, and the session
	// context a hot-added stdio server binds to — behind its own lock, off c.mu.
	// The Controller keeps the config-facing orchestration. See mcp.go.
	mcp               mcpManager
	mcpConfigureSpec  func(*plugin.Spec)
	capabilityRuntime *usecap.MCPCapabilityRuntime
	runtimeOwner      *extension.RuntimeOwner
	ablation          ablation.Set

	// goals owns the active goal's FSM (status, intercepts, idle/turn counters)
	// and its persistence, behind its own mutex so a per-turn goal save never
	// stalls an approval or status poll on c.mu. See goal.go.
	goals goalMachine

	// Base for @-refs and slash path refs, cwd for user "!" commands and command
	// discovery, and the guard root for checkpoint restore writes. Frontends read
	// it through WorkspaceRoot().
	workspaceRoot string
	// workspaceRepo is workspaceRoot's git identity, resolved before any turn ran.
	workspaceRepo gitcmd.Repo
	posture       PostureEvidence
	// refused owes the model the calls the host refused, once.
	refused promptFailureDebt

	// approval owns prompt bookkeeping and the runtime posture (ask/auto/yolo,
	// session grants, the just-approved-plan window) behind its own locks, off
	// c.mu. The Controller keeps the I/O orchestration. See approval.go.
	approval approvalManager
}

// newControllerDeps binds everything assembly settled on, once.
func newControllerDeps(opts Options, sink event.Sink, usageTee *goalUsageTee, runtimeOwner *extension.RuntimeOwner, pluginCtx context.Context) controllerDeps {
	return controllerDeps{
		taskBudget:             opts.TaskBudget,
		goalTokenBudget:        opts.GoalTokenBudget,
		goals:                  goalMachine{tokenBudget: opts.GoalTokenBudget},
		runner:                 opts.Runner,
		executor:               opts.Executor,
		guardianSess:           opts.Guardian,
		evaluator:              opts.GoalEvaluator,
		refiner:                opts.PromptRefiner,
		committer:              opts.CommitMessenger,
		goalUsageTee:           usageTee,
		sink:                   sink,
		policy:                 opts.Policy,
		observe:                cloneObserveRun(opts.Observe),
		subagentGate:           opts.SubagentGate,
		label:                  opts.Label,
		modelRef:               opts.ModelRef,
		effort:                 opts.Effort,
		providerFingerprint:    opts.ProviderFingerprint,
		modelModes:             opts.ModelModes,
		modelFace:              faceOfEntry(opts.ModelEntry),
		sessionDir:             opts.SessionDir,
		date:                   newDateAnchor(opts.Clock),
		skills:                 newSkillSet(opts.Skills, opts.AllSkills, opts.SkillStore, opts.AllSkillStore, opts.DisableImplicitSkillInvocation, opts.WorkspaceRoot),
		skillRunner:            opts.SkillRunner,
		readOnlySkillRunner:    opts.ReadOnlySkillRunner,
		skillProfile:           opts.SkillProfile,
		hooks:                  opts.Hooks,
		memory:                 newMemoryManager(opts.Memory),
		cleanup:                opts.Cleanup,
		display:                displayPrefsFrom(opts),
		disableColdResumePrune: opts.DisableColdResumePrune,
		shell:                  opts.Shell,
		onRemember:             opts.OnRemember,
		sessionRecoveryMeta:    opts.SessionRecoveryMeta,
		balance:                opts.Balance,
		feedback:               opts.Feedback,
		jobs:                   opts.Jobs,
		taskStore:              opts.TaskStore,
		workspaceLease:         opts.WorkspaceLease,
		mcp:                    newMcpManager(opts.Host, opts.Registry, pluginCtx, opts.MCPDefaultCallTimeout, hostFactNotice(opts.Executor)),
		mcpConfigureSpec:       opts.MCPConfigureSpec,
		capabilityRuntime:      opts.CapabilityRuntime,
		ablation:               opts.Ablation,
		workspaceRoot:          opts.WorkspaceRoot,
		workspaceRepo:          opts.WorkspaceRepo,
		posture:                opts.Posture,
		runtimeOwner:           runtimeOwner,
		approval:               newApprovalManager(opts.Policy, ToolApprovalAsk, opts.ApprovalTimeout, opts.OnRemember != nil),
	}
}

// publishPerProjectContext owes this session's per-project text to its first
// real turn: the skills catalog and the standing instructions. Neither is in
// the cached prefix — that is what makes the prefix identical across projects.
func (c *Controller) publishPerProjectContext(opts Options) {
}

// WorkspaceRepo is the workspace's git identity as the session resolved it when
// it opened. Host git reads the workspace through it rather than rediscovering
// the repository from files the session's own commands may have written.
func (c *Controller) WorkspaceRepo() gitcmd.Repo { return c.workspaceRepo }

// Executor returns the underlying agent when present (nil for pure runners).
func (c *Controller) Executor() *agent.Agent {
	if c == nil {
		return nil
	}
	return c.executor
}
