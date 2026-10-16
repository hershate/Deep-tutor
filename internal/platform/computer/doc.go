// Package computer operates approved applications through a pinned CUA Driver.
// Reasonix owns application and foreground authorization; CUA owns native input.
// Window targets, element tokens and capture pixels are scoped to one private
// connection and session. Reconnect invalidates prior observations. Operations
// never escalate from background to foreground implicitly. Delivery and target
// effect remain separate facts, and uncertain effects stop a batch for inspection.
package computer
