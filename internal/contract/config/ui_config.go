package config

// UIConfig controls CLI presentation-only settings. Desktop appearance is kept in
// DesktopConfig so desktop preferences cannot alter terminal output or prompts.
type UIConfig struct {
	Theme            string `toml:"theme"`              // auto|dark|light; empty resolves to auto
	ThemeStyle       string `toml:"theme_style"`        // graphite|aurora|slate|carbon|nocturne|amber and legacy aliases
	ShortcutLayout   string `toml:"shortcut_layout"`    // classic|desktop; accepted for compatibility
	CloseBehavior    string `toml:"close_behavior"`     // legacy desktop close behavior; prefer desktop.close_behavior
	ShowReasoning    bool   `toml:"show_reasoning"`     // Ctrl+O / /verbose: show thinking text in CLI; false = collapsed
	ShowTurnUsage    bool   `toml:"show_turn_usage"`    // show per-request token/cost receipts in the CLI/TUI transcript
	ShowChangedFiles bool   `toml:"show_changed_files"` // Studio: the "N files changed" pill above the composer; false = the branch chip's small count
	FoldTurnWork     bool   `toml:"fold_turn_work"`     // Studio: fold a turn's intermediate work (tool calls, thinking, middle sentences) under one line and keep the last sentence out; false = every step stays drawn
	ShowReceipt      bool   `toml:"show_receipt"`       // draw the end-of-turn delivery receipt card; false = one line when something is unproven
	CursorShape      string `toml:"cursor_shape"`       // block|underline|bar; empty defaults to bar
	CommandMode      string `toml:"commandmode"`        // ""|vi; vi gives the composer a vi command mode (empty = insert-always)
}
