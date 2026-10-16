package shellsafe

import "testing"

func TestArgsMakeReadOnlyCommandWrite(t *testing.T) {
	cases := []struct {
		name  string
		base  string
		sub   string
		argv  []string
		write bool
	}{
		{"plain find reads", "find", "", []string{"find", ".", "-name", "*.go"}, false},
		{"find -delete writes", "find", "", []string{"find", ".", "-delete"}, true},
		{"find -exec writes", "find", "", []string{"find", ".", "-exec", "rm", "{}", ";"}, true},
		{"find -exec of a reader reads", "find", "", []string{"find", "/x", "-type", "f", "-exec", "wc", "-c", "{}", "+"}, false},
		{"find -exec of a reader, one at a time", "find", "", []string{"find", ".", "-exec", "grep", "-l", "TODO", "{}", ";"}, false},
		{"find -execdir of a reader reads", "find", "", []string{"find", ".", "-execdir", "head", "-n", "3", "{}", "+"}, false},
		{"two readers in one find read", "find", "", []string{"find", ".", "-exec", "stat", "{}", ";", "-exec", "file", "{}", "+"}, false},
		{"a reader among other primaries reads", "find", "", []string{"find", ".", "-name", "*.go", "-exec", "cat", "{}", ";", "-print"}, false},
		{"find -exec rm writes", "find", "", []string{"find", ".", "-exec", "rm", "{}", "+"}, true},
		{"find -exec mv writes", "find", "", []string{"find", ".", "-exec", "mv", "{}", "/tmp", ";"}, true},
		{"find -exec sed -i writes", "find", "", []string{"find", ".", "-exec", "sed", "-i", "s/a/b/", "{}", ";"}, true},
		{"find -exec sh -c writes", "find", "", []string{"find", ".", "-exec", "sh", "-c", "wc -c \"$1\"", "_", "{}", ";"}, true},
		{"find -exec bash writes", "find", "", []string{"find", ".", "-exec", "bash", "-c", "echo hi", ";"}, true},
		{"find -execdir rm writes", "find", "", []string{"find", ".", "-execdir", "rm", "{}", ";"}, true},
		{"find -ok of a reader still writes", "find", "", []string{"find", ".", "-ok", "wc", "{}", ";"}, true},
		{"find -okdir of a reader still writes", "find", "", []string{"find", ".", "-okdir", "wc", "{}", ";"}, true},
		{"find running each file as a program writes", "find", "", []string{"find", ".", "-exec", "{}", ";"}, true},
		{"find running a path-qualified reader writes", "find", "", []string{"find", ".", "-exec", "./wc", "{}", ";"}, true},
		{"find running an absolute reader writes", "find", "", []string{"find", ".", "-exec", "/usr/bin/wc", "{}", ";"}, true},
		{"find -exec with no terminator writes", "find", "", []string{"find", ".", "-exec", "wc", "{}"}, true},
		{"find -exec with nothing to run writes", "find", "", []string{"find", ".", "-exec", ";"}, true},
		{"find -exec at the end writes", "find", "", []string{"find", ".", "-exec"}, true},
		{"a reader then -delete writes", "find", "", []string{"find", ".", "-exec", "wc", "{}", ";", "-delete"}, true},
		{"a reader then -fprint writes", "find", "", []string{"find", ".", "-exec", "wc", "{}", ";", "-fprint", "out"}, true},
		{"a reader then a writer writes", "find", "", []string{"find", ".", "-exec", "wc", "{}", ";", "-exec", "rm", "{}", ";"}, true},
		{"find -exec of a reader that writes with its args", "find", "", []string{"find", ".", "-exec", "sort", "-o", "out", "{}", ";"}, true},
		{"find -exec env running a program writes", "find", "", []string{"find", ".", "-exec", "env", "rm", "{}", ";"}, true},
		{"find -exec find -delete writes", "find", "", []string{"find", ".", "-exec", "find", "{}", "-delete", ";"}, true},
		{"find -exec find -exec rm writes", "find", "", []string{"find", ".", "-exec", "find", "{}", "-exec", "rm", "{}", ";", ";"}, true},
		{"find -exec xargs writes", "find", "", []string{"find", ".", "-exec", "xargs", "wc", "{}", ";"}, true},
		{"find -exec git commit writes", "find", "", []string{"find", ".", "-exec", "git", "commit", "-am", "x", ";"}, true},
		{"find -exec git log reads", "find", "", []string{"find", ".", "-exec", "git", "log", "-1", "{}", ";"}, false},
		{"a + that does not follow {} is an argument: sort + -o writes", "find", "", []string{"find", "in.txt", "-exec", "sort", "+", "-o", "out.txt", "{}", ";"}, true},
		{"a + that does not follow {} is an argument: sort + --output= writes", "find", "", []string{"find", "in.txt", "-exec", "sort", "+", "--output=out.txt", "{}", ";"}, true},
		{"after a stray + the rest up to ; are the reader's own arguments", "find", "", []string{"find", ".", "-exec", "wc", "+", "-delete", "{}", ";"}, false},
		{"after a stray + a writer's own argument still counts", "find", "", []string{"find", ".", "-exec", "wc", "+", "-exec", "rm", "{}", ";", ";"}, false},
		{"a primary after the real terminator is still scanned", "find", "", []string{"find", ".", "-exec", "wc", "+", "{}", ";", "-delete"}, true},
		{"a + that does not follow {} with no ; has no terminator", "find", "", []string{"find", ".", "-exec", "wc", "+", "{}"}, true},
		{"a leading + is not a terminator", "find", "", []string{"find", ".", "-exec", "+", "wc", "{}", ";"}, true},
		{"wc -c {} + still reads", "find", "", []string{"find", "/x", "-type", "f", "-exec", "wc", "-c", "{}", "+"}, false},
		{"a + after {} closes the argv and later primaries are find's", "find", "", []string{"find", ".", "-exec", "wc", "{}", "+", "-print"}, false},
		{"plain sort reads", "sort", "", []string{"sort", "-n", "f"}, false},
		{"sort -o writes", "sort", "", []string{"sort", "-o", "out", "f"}, true},
		{"sort -ofile writes", "sort", "", []string{"sort", "-oout", "f"}, true},
		{"git diff reads", "git", "diff", []string{"git", "diff", "f"}, false},
		{"git diff --output writes", "git", "diff", []string{"git", "diff", "--output=x", "f"}, true},
		{"go env reads", "go", "env", []string{"go", "env", "GOPATH"}, false},
		{"go env -w writes", "go", "env", []string{"go", "env", "-w", "GOFLAGS=-mod=mod"}, true},
		{"bare git tag lists", "git", "tag", []string{"git", "tag"}, false},
		{"git tag -l lists", "git", "tag", []string{"git", "tag", "-l", "v1.*"}, false},
		{"git tag with a name creates", "git", "tag", []string{"git", "tag", "v1.0.0"}, true},
		{"git tag -d deletes", "git", "tag", []string{"git", "tag", "-d", "v1.0.0"}, true},
		{"git tag --sort still lists", "git", "tag", []string{"git", "tag", "--sort=-v:refname"}, false},
		{"a reader with no rule", "cat", "", []string{"cat", "f"}, false},
		{"empty argv", "cat", "", nil, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := ArgsMakeReadOnlyCommandWrite(tc.base, tc.sub, tc.argv); got != tc.write {
				t.Errorf("ArgsMakeReadOnlyCommandWrite(%q, %q, %v) = %v, want %v", tc.base, tc.sub, tc.argv, got, tc.write)
			}
		})
	}
}

// The rule used to live in three packages, and they had already drifted: only
// permission knew that `git tag v1.0.0` writes a ref. Whatever the tables say,
// every classifier must now say it together.
func TestGitTagCreationIsAWriteEverywhere(t *testing.T) {
	base, sub, fields, ok := ClassifyReadOnlyCommand("git tag v1.0.0")
	if !ok {
		t.Fatal("git tag must classify against the read-only tables so its arguments get judged")
	}
	if !ArgsMakeReadOnlyCommandWrite(base, sub, fields) {
		t.Error("creating a tag writes the ref namespace")
	}
	base, sub, fields, ok = ClassifyReadOnlyCommand("git tag -l")
	if !ok || ArgsMakeReadOnlyCommandWrite(base, sub, fields) {
		t.Error("listing tags reads")
	}
}
