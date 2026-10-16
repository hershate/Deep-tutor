package shellsafe

import "strings"

// findWriters are the primaries that write or ask a person before running a
// program. -ok and -okdir read their answer from stdin, which nothing here can
// vouch for, so they stay writers even over a reader.
var findWriters = map[string]bool{
	"-delete": true, "-ok": true, "-okdir": true,
	"-fls": true, "-fprint": true, "-fprint0": true, "-fprintf": true,
}

// findRule is findWrites, bound at init: judging the program find runs reads
// argRules back, and a direct reference would make the table depend on itself.
var findRule func(args []string) bool

func init() { findRule = findWrites }

// findWrites reports whether a find call writes or runs something that might.
// -exec and -execdir hand find an argv to run: when that argv is itself a
// known read-only call, find only reads, so the program that is run decides.
// Anything the tables do not name, a program named by a path or by {}, a
// missing terminator and every other primary above are writers.
func findWrites(args []string) bool {
	for i := 0; i < len(args); i++ {
		switch arg := args[i]; {
		case findWriters[arg]:
			return true
		case arg == "-exec" || arg == "-execdir":
			end := findExecEnd(args, i+1)
			if end < 0 || !findExecIsReadOnly(args[i+1:end]) {
				return true
			}
			i = end
		}
	}
	return false
}

// findExecEnd is the index of the token that closes an -exec argv, or -1.
// find closes it at the first ; and at a + only when the token before is {};
// any other + is an ordinary argument and the argv runs on, so the argv judged
// here is exactly the argv find runs.
func findExecEnd(args []string, from int) int {
	for j := from; j < len(args); j++ {
		if args[j] == ";" || (args[j] == "+" && j > from && args[j-1] == "{}") {
			return j
		}
	}
	return -1
}

// findExecIsReadOnly judges the argv find would run. The program must be a
// bare name from the read-only tables: a path can name a file in the tree being
// searched, and {} names whatever find found.
func findExecIsReadOnly(argv []string) bool {
	if len(argv) == 0 || strings.ContainsAny(argv[0], "/\\{}") {
		return false
	}
	base, sub, fields, ok := ClassifyReadOnlyFields(argv)
	return ok && !ArgsMakeReadOnlyCommandWrite(base, sub, fields)
}
