package serve

import "time"

// ApprovalTimeout bounds how long a served session waits for a decision that
// arrives over the network. The browser is the decision channel, and a browser
// that closes takes the channel with it: unbounded, the session parks on a
// prompt nobody can answer and holds its controller for good. Expiry denies
// the prompt — fail-closed, and the answer a closed window gave anyway.
const ApprovalTimeout = 10 * time.Minute

// SetApprovalTimeout records how long this server's sessions wait for a
// decision. The value must be the one the controller was built with: a
// rebuild inherits it from here, and a prompt that outlives its browser is
// denied rather than parked.
func (s *Server) SetApprovalTimeout(d time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.approvalTimeout = d
}

// approvalDeadline is the wait this server's sessions give a decision. A
// rebuild reads it here so the replacement runtime inherits the bound the
// outgoing one ran under.
func (s *Server) approvalDeadline() time.Duration {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.approvalTimeout
}
