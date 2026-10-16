package control

import "reasonix/internal/contract/event"

// inboxEventSink observes Steer and unapplied-steer events so durable inbox
// state tracks consumption without frontends matching on text, and names the
// stored attachments a person's steer references.
type inboxEventSink struct {
	// Every optional capability travels through the embedded forwarder. This
	// wrapper observes two event kinds; hand-written forwarding made it the
	// place a delegation receipt silently ended.
	event.AuditForwarder
	c *Controller
}

func (s *inboxEventSink) Emit(e event.Event) {
	if s == nil {
		return
	}
	if s.c != nil && e.Kind == event.Steer && !e.HostAuthored {
		e.Attachments = s.c.StoredAttachments(e.Text)
	}
	if s.Inner != nil {
		s.Inner.Emit(e)
	}
	if s.c == nil {
		return
	}
	switch e.Kind {
	case event.ToolResult:
		s.c.noteRefusedCall(e.Tool)
	case event.Steer:
		if e.ItemID != "" {
			s.c.onInboxSteerConsumed(e.ItemID)
		}
	case event.Notice:
		if e.Code == event.NoticeCodeUnappliedSteer && e.ItemID != "" {
			s.c.onInboxUnappliedSteer(e.ItemID)
		}
	case event.CompactionDone:
		// The listing rode a user turn the fold may have just summarised away.
		// Standing state that is delivered once has to be re-owed when the turn
		// carrying it stops being verbatim.
		s.c.date.debt.forget()
		s.c.skills.forgetDeliveredCatalog()
		s.c.memory.forgetDeliveredInstructions()
	}
}
