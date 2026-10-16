package termrender

import "testing"

func TestStatusSequenceWireFormat(t *testing.T) {
	cases := []struct {
		state ProgramState
		kind  BlockedKind
		want  string
	}{
		{StateWorking, BlockedNone, "\x1b]7501;state=working:app=reasonix\x1b\\"},
		{StateBlocked, BlockedPermission, "\x1b]7501;state=blocked:kind=permission:app=reasonix\x1b\\"},
		{StateBlocked, BlockedQuestion, "\x1b]7501;state=blocked:kind=question:app=reasonix\x1b\\"},
	}
	for _, c := range cases {
		if got := StatusSequence(c.state, c.kind); got != c.want {
			t.Errorf("StatusSequence(%s,%s) = %q, want %q", c.state, c.kind, got, c.want)
		}
	}
}
