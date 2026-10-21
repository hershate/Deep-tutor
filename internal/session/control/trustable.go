package control

// trustableFolder answers whether root may be trusted as a whole for this
// session. It is the process-level TrustableFolder plus this session's own
// storage: a session that states a home must not be handed its own home.
// Both halves are checked, not one: a binding narrows what a session can see,
// but it must not widen what it can reach.
func (c *Controller) trustableFolder(root string) bool {
	if _, refused := c.roots.FolderGrantRefusal(root); refused {
		return false
	}
	return TrustableFolder(root)
}

// trustHome is where this session records and reads a folder's trust and the
// rules it remembered for it. One answer, not two: the trust decision and the
// remembered rules are two views of one record, and reading them from two
// homes means the rule the person granted is not the rule the session shows.
// The session's storage binding is the authority when it states a home — that
// is the home the person's own decision belongs to — and the posture's home is
// the fallback for a host that states only that.
func (c *Controller) trustHome() string {
	if home := c.roots.StatedHome(); home != "" {
		return home
	}
	if home := c.posture.Home; home != "" {
		return home
	}
	return c.roots.Home()
}
