package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
)

type wireConstantMirror struct{ goFile, goName, tsFile, tsName string }

var mirroredWireConstants = []wireConstantMirror{
	{"internal/contract/event/notice_codes.go", "NoticeCodeSessionTreeChanged", "desktop/frontend-next/src/port/tree_events.ts", "NoticeCodeSessionTreeChanged"},
}

func wireConstantFindings(root string, mirrors []wireConstantMirror) []Finding {
	var out []Finding
	for _, m := range mirrors {
		goBody, goErr := os.ReadFile(filepath.Join(root, m.goFile))
		tsBody, tsErr := os.ReadFile(filepath.Join(root, m.tsFile))
		value := ""
		if goErr == nil {
			src := parseBytes(m.goFile, goBody)
			ast.Inspect(src.file, func(n ast.Node) bool {
				decl, ok := n.(*ast.GenDecl)
				if !ok || decl.Tok != token.CONST {
					return true
				}
				for _, spec := range decl.Specs {
					s := spec.(*ast.ValueSpec)
					for i, name := range s.Names {
						if name.Name != m.goName || i >= len(s.Values) {
							continue
						}
						if literal, ok := s.Values[i].(*ast.BasicLit); ok && literal.Kind == token.STRING {
							value, _ = strconv.Unquote(literal.Value)
						}
					}
				}
				return false
			})
		}
		pattern := regexp.MustCompile(`export\s+const\s+` + regexp.QuoteMeta(m.tsName) + `\s*=\s*"([^"]*)"`)
		match := pattern.FindSubmatch(tsBody)
		if goErr != nil || tsErr != nil || value == "" || len(match) != 2 || string(match[1]) != value {
			out = append(out, Finding{m.goFile, 1, ruleWireParity, fmt.Sprintf("%s and %s.%s must declare the same wire constant", m.goName, m.tsFile, m.tsName), 1})
		}
	}
	return out
}
