package termrender

// ProgramState is a state of the OSC 7501 Program Status Protocol.
type ProgramState string

const (
	StateIdle    ProgramState = "idle"
	StateWorking ProgramState = "working"
	StateDone    ProgramState = "done"
	StateBlocked ProgramState = "blocked"
)

// BlockedKind says what a blocked program is waiting for.
type BlockedKind string

const (
	BlockedNone       BlockedKind = ""
	BlockedPermission BlockedKind = "permission"
	BlockedQuestion   BlockedKind = "question"
)

// StatusSequence is the OSC 7501 record a program writes to tell its terminal
// its state. Terminals that do not know the sequence ignore it.
func StatusSequence(state ProgramState, kind BlockedKind) string {
	body := "state=" + string(state)
	if kind != BlockedNone {
		body += ":kind=" + string(kind)
	}
	return "\x1b]7501;" + body + ":app=reasonix\x1b\\"
}

// StatusClear removes the record, for a program that is exiting.
const StatusClear = "\x1b]7501;state=clear\x1b\\"
