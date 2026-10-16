package tutor

import (
	"bytes"
	"compress/zlib"
	"errors"
	"fmt"
	"io"
	"maps"
	"strconv"
	"strings"
)

// pdf.go holds a byte-level PDF reader: a lexer for the object syntax, an
// object table built by scanning declarations (classic xref, xref streams
// and object streams all yield their objects), and Flate decoding.

// pdfObj is any PDF object value: nil, bool, int64, float64, string,
// pdfHexString, pdfName, pdfRef, []pdfObj, pdfDict or *pdfStream.
type pdfObj = any

// pdfRef names an indirect object.
type pdfRef struct{ num, gen int }

// pdfStream is a stream object: its dict plus the encoded payload.
type pdfStream struct {
	dict pdfDict
	raw  []byte
}

// pdfDict keys drop the name's leading slash.
type pdfDict map[string]pdfObj

type pdfName string
type pdfHexString []byte

// pdfLexer walks PDF syntax tokens.
type pdfLexer struct {
	data []byte
	pos  int
}

type pdfTokenKind int

const (
	pdfTokEOF pdfTokenKind = iota
	pdfTokValue
	pdfTokKeyword
)

type pdfToken struct {
	kind    pdfTokenKind
	keyword string // for pdfTokKeyword: obj, endobj, stream, …
	value   pdfObj
}

func isPDFWhitespace(c byte) bool {
	return c == 0 || c == '\t' || c == '\n' || c == '\f' || c == '\r' || c == ' '
}

func isPDFDelimiter(c byte) bool {
	return strings.IndexByte("()<>[]{}/%", c) >= 0
}

func (l *pdfLexer) skipSpace() {
	for l.pos < len(l.data) {
		c := l.data[l.pos]
		if isPDFWhitespace(c) {
			l.pos++
			continue
		}
		if c == '%' {
			for l.pos < len(l.data) && l.data[l.pos] != '\n' && l.data[l.pos] != '\r' {
				l.pos++
			}
			continue
		}
		return
	}
}

// next returns the next token. Keywords and the structural brackets come back
// as pdfTokKeyword; everything else decodes to a value.
func (l *pdfLexer) next() pdfToken {
	l.skipSpace()
	if l.pos >= len(l.data) {
		return pdfToken{kind: pdfTokEOF}
	}
	c := l.data[l.pos]
	switch c {
	case '(':
		s, end := l.readLiteralString()
		l.pos = end
		return pdfToken{kind: pdfTokValue, value: s}
	case '<':
		if l.pos+1 < len(l.data) && l.data[l.pos+1] == '<' {
			l.pos += 2
			return pdfToken{kind: pdfTokKeyword, keyword: "<<"}
		}
		h, end := l.readHexString()
		l.pos = end
		return pdfToken{kind: pdfTokValue, value: h}
	case '>':
		if l.pos+1 < len(l.data) && l.data[l.pos+1] == '>' {
			l.pos += 2
			return pdfToken{kind: pdfTokKeyword, keyword: ">>"}
		}
		l.pos++
		return pdfToken{kind: pdfTokKeyword, keyword: ">"}
	case '[':
		l.pos++
		return pdfToken{kind: pdfTokKeyword, keyword: "["}
	case ']':
		l.pos++
		return pdfToken{kind: pdfTokKeyword, keyword: "]"}
	case '/':
		l.pos++
		start := l.pos
		for l.pos < len(l.data) {
			d := l.data[l.pos]
			if isPDFWhitespace(d) || isPDFDelimiter(d) {
				break
			}
			l.pos++
		}
		return pdfToken{kind: pdfTokValue, value: pdfName(decodePDFName(l.data[start:l.pos]))}
	case ')', '{', '}':
		l.pos++
		return l.next()
	default:
		return l.nextWord()
	}
}

// nextWord reads a bare word: a number, a boolean, null, or an operator
// keyword.
func (l *pdfLexer) nextWord() pdfToken {
	start := l.pos
	for l.pos < len(l.data) {
		d := l.data[l.pos]
		if isPDFWhitespace(d) || isPDFDelimiter(d) {
			break
		}
		l.pos++
	}
	word := string(l.data[start:l.pos])
	if word == "" {
		l.pos++
		return pdfToken{kind: pdfTokEOF}
	}
	switch word {
	case "true":
		return pdfToken{kind: pdfTokValue, value: true}
	case "false":
		return pdfToken{kind: pdfTokValue, value: false}
	case "null":
		return pdfToken{kind: pdfTokValue, value: nil}
	}
	if word[0] == '+' || word[0] == '-' || word[0] == '.' || (word[0] >= '0' && word[0] <= '9') {
		if n, err := strconv.ParseInt(word, 10, 64); err == nil {
			return pdfToken{kind: pdfTokValue, value: n}
		}
		if f, err := strconv.ParseFloat(word, 64); err == nil {
			return pdfToken{kind: pdfTokValue, value: f}
		}
	}
	return pdfToken{kind: pdfTokKeyword, keyword: word}
}

func decodePDFName(raw []byte) string {
	var sb strings.Builder
	for i := 0; i < len(raw); i++ {
		if raw[i] == '#' && i+2 < len(raw) {
			if v, err := strconv.ParseUint(string(raw[i+1:i+3]), 16, 8); err == nil {
				sb.WriteByte(byte(v))
				i += 2
				continue
			}
		}
		sb.WriteByte(raw[i])
	}
	return sb.String()
}

// readLiteralString decodes (…) with escapes; the return excludes the braces.
func (l *pdfLexer) readLiteralString() (string, int) {
	// Size from the next delimiter when one is in sight: the append chain
	// from nil re-copied every literal string several times over.
	est := bytes.IndexByte(l.data[l.pos+1:], ')')
	if est < 0 || est > 1<<16 {
		est = 64
	}
	out := make([]byte, 0, est+1)
	depth := 1
	i := l.pos + 1
	for i < len(l.data) {
		c := l.data[i]
		switch c {
		case '\\':
			if i+1 >= len(l.data) {
				return string(out), i + 1
			}
			e := l.data[i+1]
			i += 2
			switch e {
			case 'n':
				out = append(out, '\n')
			case 'r':
				out = append(out, '\r')
			case 't':
				out = append(out, '\t')
			case 'b':
				out = append(out, '\b')
			case 'f':
				out = append(out, '\f')
			case '(', ')', '\\':
				out = append(out, e)
			case '\n':
			case '\r':
				if i < len(l.data) && l.data[i] == '\n' {
					i++
				}
			default:
				if e >= '0' && e <= '7' {
					oct := []byte{e}
					for len(oct) < 3 && i < len(l.data) && l.data[i] >= '0' && l.data[i] <= '7' {
						oct = append(oct, l.data[i])
						i++
					}
					if v, err := strconv.ParseUint(string(oct), 8, 8); err == nil {
						out = append(out, byte(v))
					}
				} else {
					out = append(out, e)
				}
			}
		case '(':
			depth++
			out = append(out, c)
			i++
		case ')':
			depth--
			if depth == 0 {
				return string(out), i + 1
			}
			out = append(out, c)
			i++
		default:
			out = append(out, c)
			i++
		}
	}
	return string(out), i
}

// readHexString decodes <…> ignoring whitespace; an odd tail drops its last
// nibble, as the specification allows.
func (l *pdfLexer) readHexString() (pdfHexString, int) {
	i := l.pos + 1
	var hex []byte
	for i < len(l.data) && l.data[i] != '>' {
		if !isPDFWhitespace(l.data[i]) {
			hex = append(hex, l.data[i])
		}
		i++
	}
	if len(hex)%2 == 1 {
		hex = append(hex, '0')
	}
	out := make([]byte, len(hex)/2)
	for i := range out {
		v, err := strconv.ParseUint(string(hex[2*i:2*i+2]), 16, 8)
		if err != nil {
			return out, len(l.data)
		}
		out[i] = byte(v)
	}
	if i < len(l.data) {
		i++
	}
	return out, i
}

// value parses one value: dict, array or scalar, folding "num gen R" into a
// pdfRef. Brackets arrive as keywords, so the caller drives structure.
func (l *pdfLexer) value() (pdfObj, error) {
	tok := l.next()
	return l.valueFrom(tok)
}

func (l *pdfLexer) valueFrom(tok pdfToken) (pdfObj, error) {
	if tok.kind == pdfTokKeyword {
		switch tok.keyword {
		case "<<":
			return l.dict()
		case "[":
			return l.array()
		}
		return nil, fmt.Errorf("pdf: unexpected keyword %q", tok.keyword)
	}
	if n, ok := tok.value.(int64); ok {
		before := l.pos
		genTok := l.next()
		if g, isGen := genTok.value.(int64); isGen && genTok.kind == pdfTokValue {
			refTok := l.next()
			if refTok.kind == pdfTokKeyword && refTok.keyword == "R" {
				return pdfRef{num: int(n), gen: int(g)}, nil
			}
		}
		l.pos = before
		return n, nil
	}
	return tok.value, nil
}

func (l *pdfLexer) dict() (pdfObj, error) {
	out := pdfDict{}
	for {
		tok := l.next()
		if tok.kind == pdfTokEOF {
			return out, nil
		}
		if tok.kind == pdfTokKeyword && tok.keyword == ">>" {
			return out, nil
		}
		name, ok := tok.value.(pdfName)
		if !ok {
			continue
		}
		val, err := l.value()
		if err != nil {
			return out, err
		}
		out[string(name)] = val
	}
}

func (l *pdfLexer) array() (pdfObj, error) {
	var out []pdfObj
	for {
		tok := l.next()
		if tok.kind == pdfTokEOF {
			return out, nil
		}
		if tok.kind == pdfTokKeyword && tok.keyword == "]" {
			return out, nil
		}
		v, err := l.valueFrom(tok)
		if err != nil {
			return out, err
		}
		out = append(out, v)
	}
}

// pdfFile is the loaded object table.
type pdfFile struct {
	objs map[int]pdfObj
}

// loadPDF scans every "N G obj" declaration and expands object streams, so
// the table holds plain and compressed objects alike.
func loadPDF(data []byte) (*pdfFile, error) {
	if !bytes.Contains(data, []byte("%PDF-")) {
		return nil, errors.New("pdf: missing %PDF- header")
	}
	if bytes.Contains(data, []byte("/Encrypt")) {
		return nil, errors.New("pdf: encrypted document")
	}
	f := &pdfFile{objs: map[int]pdfObj{}}
	lex := &pdfLexer{data: data}
	for {
		tok := lex.next()
		if tok.kind == pdfTokEOF {
			break
		}
		num, isInt := tok.value.(int64)
		if !isInt {
			continue
		}
		afterNum := lex.pos
		genTok := lex.next()
		_, isGen := genTok.value.(int64)
		objTok := lex.next()
		if !isGen || objTok.kind != pdfTokKeyword || objTok.keyword != "obj" {
			// Not an object header: rewind to just after the first integer
			// so a genuine header starting there is still seen.
			lex.pos = afterNum
			continue
		}
		value, err := lex.value()
		if err != nil {
			continue
		}
		d, isDict := value.(pdfDict)
		streamTok := lex.next()
		if isDict && streamTok.kind == pdfTokKeyword && streamTok.keyword == "stream" {
			stream, err := readPDFStream(lex, data, d)
			if err != nil {
				continue
			}
			value = stream
		}
		f.objs[int(num)] = value
	}
	if len(f.objs) == 0 {
		return nil, errors.New("pdf: no objects found")
	}
	f.expandObjectStreams()
	return f, nil
}

// readPDFStream consumes "stream\n…endstream", trusting the nearest
// endstream marker over any /Length the file claims.
func readPDFStream(lex *pdfLexer, data []byte, d pdfDict) (pdfObj, error) {
	if lex.pos < len(data) && data[lex.pos] == '\r' {
		lex.pos++
	}
	if lex.pos < len(data) && data[lex.pos] == '\n' {
		lex.pos++
	}
	idx := bytes.Index(data[lex.pos:], []byte("endstream"))
	if idx < 0 {
		return nil, errors.New("pdf: unterminated stream")
	}
	raw := data[lex.pos : lex.pos+idx]
	raw = bytes.TrimRight(raw, "\r\n")
	lex.pos += idx + len("endstream")
	return &pdfStream{dict: d, raw: raw}, nil
}

// expandObjectStreams decodes /Type /ObjStm streams and registers the objects
// packed inside them.
func (f *pdfFile) expandObjectStreams() {
	var containers []*pdfStream
	for _, obj := range f.objs {
		stream, ok := obj.(*pdfStream)
		if !ok {
			continue
		}
		if t, _ := f.direct(stream.dict["Type"]).(pdfName); t == "ObjStm" {
			containers = append(containers, stream)
		}
	}
	for _, stream := range containers {
		f.expandObjectStream(stream)
	}
}

func (f *pdfFile) expandObjectStream(stream *pdfStream) {
	body, err := decodePDFStream(f, stream)
	if err != nil {
		return
	}
	n, _ := f.direct(stream.dict["N"]).(int64)
	first, _ := f.direct(stream.dict["First"]).(int64)
	if n <= 0 || first <= 0 || int(first) > len(body) {
		return
	}
	header := &pdfLexer{data: body[:first]}
	pairs := make([][2]int64, 0, n)
	for range n {
		numTok := header.next()
		offTok := header.next()
		num, ok1 := numTok.value.(int64)
		off, ok2 := offTok.value.(int64)
		if !ok1 || !ok2 {
			break
		}
		pairs = append(pairs, [2]int64{num, off})
	}
	for _, pair := range pairs {
		start := int(first + pair[1])
		if start > len(body) {
			continue
		}
		inner := &pdfLexer{data: body, pos: start}
		value, err := inner.value()
		if err != nil {
			continue
		}
		f.objs[int(pair[0])] = value
	}
}

// direct resolves one indirection level; deep chains go through resolve.
func (f *pdfFile) direct(obj pdfObj) pdfObj {
	ref, ok := obj.(pdfRef)
	if !ok {
		return obj
	}
	target, exists := f.objs[ref.num]
	if !exists {
		return nil
	}
	return target
}

// resolve follows reference chains with a depth guard against cycles.
func (f *pdfFile) resolve(obj pdfObj) pdfObj {
	for range 16 {
		ref, ok := obj.(pdfRef)
		if !ok {
			return obj
		}
		obj = f.direct(ref)
		if obj == nil {
			return nil
		}
	}
	return nil
}

// decodePDFStream applies the stream's filter; FlateDecode is the one text
// and object streams carry in practice.
func decodePDFStream(f *pdfFile, s *pdfStream) ([]byte, error) {
	filter := f.resolve(s.dict["Filter"])
	if filter == nil {
		return s.raw, nil
	}
	name, ok := filter.(pdfName)
	if !ok {
		if arr, isArr := filter.([]pdfObj); isArr && len(arr) > 0 {
			name, ok = f.resolve(arr[0]).(pdfName)
		}
	}
	if !ok || name != "FlateDecode" {
		return nil, fmt.Errorf("pdf: unsupported filter %v", filter)
	}
	zr, err := zlib.NewReader(bytes.NewReader(s.raw))
	if err != nil {
		return nil, fmt.Errorf("pdf: flate: %w", err)
	}
	defer zr.Close()
	return io.ReadAll(io.LimitReader(zr, zipMaxUncompressed))
}

// catalog returns the document catalog, found by type rather than by
// trusting trailer pointers.
func (f *pdfFile) catalog() (pdfDict, error) {
	for _, obj := range f.objs {
		d, ok := f.resolve(obj).(pdfDict)
		if !ok {
			continue
		}
		if t, _ := f.resolve(d["Type"]).(pdfName); t == "Catalog" {
			return d, nil
		}
	}
	return nil, errors.New("pdf: no catalog object")
}

// pages walks the page tree, collecting leaf page dicts with inherited
// resources resolved against their ancestors.
func (f *pdfFile) pages() ([]pdfDict, error) {
	root, err := f.catalog()
	if err != nil {
		return nil, err
	}
	pagesRef, ok := root["Pages"]
	if !ok {
		return nil, errors.New("pdf: catalog names no pages")
	}
	var out []pdfDict
	var walk func(node pdfObj, inherited pdfDict)
	walk = func(node pdfObj, inherited pdfDict) {
		d, ok := f.resolve(node).(pdfDict)
		if !ok {
			return
		}
		res := pdfDict{}
		maps.Copy(res, inherited)
		for _, k := range []string{"Resources", "MediaBox", "Rotate"} {
			if v, ok := d[k]; ok {
				res[k] = v
			}
		}
		switch t, _ := f.resolve(d["Type"]).(pdfName); t {
		case "Page":
			merged := pdfDict{}
			maps.Copy(merged, d)
			for k, v := range res {
				if _, exists := merged[k]; !exists {
					merged[k] = v
				}
			}
			out = append(out, merged)
		case "Pages", "":
			kids, _ := f.resolve(d["Kids"]).([]pdfObj)
			for _, kid := range kids {
				walk(kid, res)
			}
		}
	}
	walk(pagesRef, pdfDict{})
	if len(out) == 0 {
		return nil, errors.New("pdf: page tree has no leaves")
	}
	return out, nil
}
