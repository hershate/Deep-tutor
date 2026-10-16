package tutor

import (
	"errors"
	"fmt"
	"strings"
	"unicode/utf16"

	"golang.org/x/text/encoding/charmap"
)

// pdftext.go turns page content streams into text: fonts decode through
// their ToUnicode CMaps when present, otherwise CP1252 (WinAnsi), and the
// text matrix decides line breaks — no OCR, an honest empty for scanned files.

// pdfFont decodes the bytes a content stream hands to Tj/TJ.
type pdfFont struct {
	width       int               // code width in bytes, from the CMap codespace
	cmap        map[string]string // code bytes → unicode
	simple      bool              // no ToUnicode: single-byte CP1252
	undecodable bool              // CID font without a ToUnicode map
}

func (f *pdfFont) decode(raw []byte) string {
	if f == nil {
		return decodeCP1252(raw)
	}
	if f.undecodable {
		return ""
	}
	if f.simple {
		return decodeCP1252(raw)
	}
	var sb strings.Builder
	for i := 0; i < len(raw); i += f.width {
		end := min(i+f.width, len(raw))
		if mapped, ok := f.cmap[string(raw[i:end])]; ok {
			sb.WriteString(mapped)
			continue
		}
		if f.width == 1 {
			sb.WriteString(decodeCP1252(raw[i:end]))
			continue
		}
		sb.WriteRune(0xFFFD)
	}
	return sb.String()
}

// decodeString is decode for operands the lexer already handed over as
// strings: simple fonts decode without the byte-slice copy.
func (f *pdfFont) decodeString(raw string) string {
	if f == nil || f.simple {
		return decodeCP1252String(raw)
	}
	if f.undecodable {
		return ""
	}
	return f.decode([]byte(raw))
}

var cp1252 = charmap.Windows1252.NewDecoder()

// cp1252Runes holds the byte→rune table the x/text charmap defines, walked
// directly per string instead of through the transform pipeline.
var cp1252Runes = func() [256]rune {
	var table [256]rune
	for b := range 256 {
		decoded, err := cp1252.Bytes([]byte{byte(b)})
		if err != nil || len(decoded) == 0 {
			table[b] = 0xFFFD
			continue
		}
		for _, r := range string(decoded) {
			table[b] = r
			break
		}
	}
	return table
}()

func decodeCP1252(raw []byte) string {
	return decodeCP1252String(string(raw))
}

// decodeCP1252String walks the table directly: pure-ASCII input — the bulk
// of Latin documents — returns without copying at all.
func decodeCP1252String(raw string) string {
	for i := range len(raw) {
		if raw[i] >= 0x80 {
			out := make([]rune, 0, len(raw))
			out = append(out, []rune(raw[:i])...)
			for j := i; j < len(raw); j++ {
				out = append(out, cp1252Runes[raw[j]])
			}
			return string(out)
		}
	}
	return raw
}

// utf16BE decodes CMap destination bytes.
func utf16BE(raw []byte) string {
	if len(raw)%2 == 1 {
		raw = append(append([]byte{}, raw...), 0)
	}
	units := make([]uint16, len(raw)/2)
	for i := range units {
		units[i] = uint16(raw[2*i])<<8 | uint16(raw[2*i+1])
	}
	return string(utf16.Decode(units))
}

// parseCMap reads a ToUnicode stream: codespacerange fixes the code width,
// bfchar and bfrange map codes to text. Operands arrive between the begin*
// and end* keywords, so accumulation starts at begin and applies at end.
func parseCMap(body []byte) (int, map[string]string) {
	width := 2
	mapping := map[string]string{}
	lex := &pdfLexer{data: body}
	var toks []pdfObj
	for {
		tok := lex.next()
		if tok.kind == pdfTokEOF {
			break
		}
		if tok.kind != pdfTokKeyword {
			toks = append(toks, tok.value)
			if len(toks) > 8192 {
				toks = toks[:0]
			}
			continue
		}
		switch tok.keyword {
		case "begincodespacerange", "beginbfchar", "beginbfrange":
			toks = toks[:0]
		case "endcodespacerange":
			if src, _, ok := hexPair(toks); ok {
				width = max(1, len(src))
			}
			toks = toks[:0]
		case "endbfchar":
			for i := 0; i+1 < len(toks); i += 2 {
				src, dst, ok := hexPair(toks[i : i+2])
				if ok {
					mapping[string(src)] = utf16BE(dst)
				}
			}
			toks = toks[:0]
		case "endbfrange":
			for i := 0; i+2 < len(toks); i += 3 {
				applyBFRange(toks[i:i+3], mapping)
			}
			toks = toks[:0]
		default:
			toks = toks[:0]
		}
	}
	return width, mapping
}

func hexPair(toks []pdfObj) (src, dst pdfHexString, ok bool) {
	if len(toks) < 2 {
		return nil, nil, false
	}
	a, ok1 := toks[0].(pdfHexString)
	b, ok2 := toks[1].(pdfHexString)
	if !ok1 || !ok2 || len(a) == 0 {
		return nil, nil, false
	}
	return a, b, true
}

// applyBFRange maps [lo hi dst]: either one base destination whose final
// UTF-16 unit increments across the range, or an explicit destination array.
func applyBFRange(toks []pdfObj, mapping map[string]string) {
	lo, ok1 := toks[0].(pdfHexString)
	hi, ok2 := toks[1].(pdfHexString)
	if !ok1 || !ok2 || len(lo) == 0 {
		return
	}
	loVal := beInt(lo)
	hiVal := beInt(hi)
	if hiVal < loVal || hiVal-loVal > 65535 {
		return
	}
	if dst, ok := toks[2].(pdfHexString); ok {
		units := beUnits(dst)
		if len(units) == 0 {
			return
		}
		for i := uint64(0); i <= hiVal-loVal; i++ {
			code := codeBytes(lo, loVal+i, len(lo))
			out := append([]uint16{}, units...)
			out[len(out)-1] += uint16(i)
			mapping[string(code)] = string(utf16.Decode(out))
		}
		return
	}
	if arr, ok := toks[2].([]pdfObj); ok {
		for i := 0; i < len(arr) && loVal+uint64(i) <= hiVal; i++ {
			dst, isHex := arr[i].(pdfHexString)
			if !isHex {
				continue
			}
			code := codeBytes(lo, loVal+uint64(i), len(lo))
			mapping[string(code)] = utf16BE(dst)
		}
	}
}

func beUnits(b []byte) []uint16 {
	if len(b)%2 == 1 {
		b = append(append([]byte{}, b...), 0)
	}
	units := make([]uint16, len(b)/2)
	for i := range units {
		units[i] = uint16(b[2*i])<<8 | uint16(b[2*i+1])
	}
	return units
}

func beInt(b []byte) uint64 {
	var v uint64
	for _, c := range b {
		v = v<<8 | uint64(c)
	}
	return v
}

func codeBytes(lo pdfHexString, value uint64, width int) []byte {
	out := make([]byte, width)
	for i := width - 1; i >= 0; i-- {
		out[i] = byte(value & 0xFF)
		value >>= 8
	}
	return out
}

// pageFonts builds decoders for a page's resource names.
func (f *pdfFile) pageFonts(page pdfDict) map[string]*pdfFont {
	fonts := map[string]*pdfFont{}
	res, _ := f.resolve(page["Resources"]).(pdfDict)
	fontDict, _ := f.resolve(res["Font"]).(pdfDict)
	for name, val := range fontDict {
		fd, ok := f.resolve(val).(pdfDict)
		if !ok {
			continue
		}
		font := &pdfFont{width: 1, simple: true}
		if subtype, _ := f.resolve(fd["Subtype"]).(pdfName); subtype == "Type0" {
			// CID fonts carry glyph indices; without ToUnicode they decode
			// to nothing rather than to mojibake.
			font.undecodable = true
		}
		if tounicode, present := fd["ToUnicode"]; present {
			if stream, ok := f.resolve(tounicode).(*pdfStream); ok {
				if body, err := decodePDFStream(f, stream); err == nil {
					font.width, font.cmap = parseCMap(body)
					font.simple = false
					font.undecodable = false
				}
			}
		}
		fonts[name] = font
	}
	return fonts
}

// pdfPageText runs one page's content streams through the text operators.
func (f *pdfFile) pageText(page pdfDict) string {
	fonts := f.pageFonts(page)
	var streams []*pdfStream
	switch contents := f.resolve(page["Contents"]).(type) {
	case *pdfStream:
		streams = []*pdfStream{contents}
	case []pdfObj:
		for _, item := range contents {
			if stream, ok := f.resolve(item).(*pdfStream); ok {
				streams = append(streams, stream)
			}
		}
	}
	ex := &pdfTextState{fonts: fonts}
	for _, stream := range streams {
		body, err := decodePDFStream(f, stream)
		if err != nil {
			continue
		}
		ex.run(body)
	}
	return ex.finish()
}

// pdfTextState is the interpreter state: operand stack, current font, and
// the text matrices that decide line breaks.
type pdfTextState struct {
	fonts     map[string]*pdfFont
	current   *pdfFont
	out       []string
	line      strings.Builder
	tlm, tm   [6]float64
	leading   float64
	lineY     float64
	haveLineY bool
	moved     bool
}

var identityMatrix = [6]float64{1, 0, 0, 1, 0, 0}

func (x *pdfTextState) run(content []byte) {
	// A page's lines fit one buffer; growing from empty re-copied each line
	// through the builder's doubling chain.
	x.line.Grow(min(2048, len(content)/8))
	lex := &pdfLexer{data: content}
	var operands []pdfObj
	for {
		tok := lex.next()
		if tok.kind == pdfTokEOF {
			break
		}
		if tok.kind == pdfTokValue {
			operands = append(operands, tok.value)
			continue
		}
		if tok.keyword == "[" {
			arr, _ := lex.array()
			operands = append(operands, arr)
			continue
		}
		x.operate(tok.keyword, operands)
		operands = operands[:0]
	}
}

func (x *pdfTextState) operate(op string, operands []pdfObj) {
	num := func(i int) float64 {
		if i >= len(operands) {
			return 0
		}
		switch v := operands[i].(type) {
		case int64:
			return float64(v)
		case float64:
			return v
		}
		return 0
	}
	switch op {
	case "BT":
		x.tlm, x.tm = identityMatrix, identityMatrix
		x.moved, x.haveLineY = true, false
	case "Tf":
		if len(operands) >= 1 {
			if name, ok := operands[0].(pdfName); ok {
				x.current = x.fonts[string(name)]
			}
		}
	case "TL":
		x.leading = num(0)
	case "Tm":
		for i := range 6 {
			x.tlm[i] = num(i)
		}
		x.tm = x.tlm
		x.moved = true
	case "Td", "TD":
		tx, ty := num(0), num(1)
		if op == "TD" {
			x.leading = -ty
		}
		x.tlm[4] += tx*x.tlm[0] + ty*x.tlm[2]
		x.tlm[5] += tx*x.tlm[1] + ty*x.tlm[3]
		x.tm = x.tlm
		x.moved = true
	case "T*":
		// T* means "next line" even when the leading is zero or the writer
		// forgot TL; a matrix-only heuristic would fuse the lines.
		x.lineBreak()
		x.tlm[5] -= x.leading
		x.tm = x.tlm
		x.moved = true
	case "Tj":
		if len(operands) > 0 {
			x.showOperand(operands[0])
		}
	case "'":
		x.lineBreak()
		if len(operands) > 0 {
			x.showOperand(operands[0])
		}
	case `"`:
		x.lineBreak()
		if len(operands) > 2 {
			x.showOperand(operands[2])
		}
	case "TJ":
		if len(operands) > 0 {
			if arr, ok := operands[0].([]pdfObj); ok {
				for _, item := range arr {
					x.showOperand(item)
				}
			}
		}
	case "ET":
		x.lineBreak()
	}
}

// showOperand shows one text-bearing operand; content streams may spell the
// string as a literal or as a hex string.
func (x *pdfTextState) showOperand(v pdfObj) {
	switch s := v.(type) {
	case string:
		x.show(s)
	case pdfHexString:
		x.showBytes(s)
	default:
	}
}

func (x *pdfTextState) show(text string) {
	x.breakIfMoved()
	x.line.WriteString(x.current.decodeString(text))
}

func (x *pdfTextState) showBytes(raw []byte) {
	x.breakIfMoved()
	x.line.WriteString(x.current.decode(raw))
}

func (x *pdfTextState) breakIfMoved() {
	if x.moved && x.line.Len() > 0 {
		if !x.haveLineY || abs64(x.tm[5]-x.lineY) > 3 {
			x.lineBreak()
		}
	}
	if !x.haveLineY {
		x.lineY, x.haveLineY = x.tm[5], true
	}
	x.moved = false
}

func (x *pdfTextState) lineBreak() {
	if x.line.Len() > 0 {
		x.out = append(x.out, x.line.String())
		x.line.Reset()
	}
	x.haveLineY = false
}

func (x *pdfTextState) finish() string {
	x.lineBreak()
	kept := x.out[:0]
	for _, line := range x.out {
		if strings.TrimSpace(line) != "" {
			kept = append(kept, line)
		}
	}
	return strings.Join(kept, "\n")
}

func abs64(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

// parsePDF extracts the text layer: best effort, with an honest error for
// scanned files whose content streams carry no readable text.
func parsePDF(data []byte) (*ParsedMaterial, error) {
	file, err := loadPDF(data)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnsupportedFormat, err)
	}
	pages, err := file.pages()
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnsupportedFormat, err)
	}
	// Pages share the file's object map read-only — no parse-time lazy
	// caching — so each page's content streams run on its own interpreter
	// state in parallel, landing back in page order.
	pageTexts := make([]string, len(pages))
	parallelFor(len(pages), func(i int) {
		if text := file.pageText(pages[i]); strings.TrimSpace(text) != "" {
			pageTexts[i] = text
		}
	})
	texts := make([]string, 0, len(pages))
	for _, text := range pageTexts {
		if text != "" {
			texts = append(texts, text)
		}
	}
	if len(texts) == 0 {
		return nil, errors.New("tutor: pdf text layer is empty (scanned or image-only pdf?)")
	}
	joined := strings.Join(texts, "\n\n")
	if !printableText(joined) {
		return nil, errors.New("tutor: pdf text layer is unreadable (encoded glyphs without a ToUnicode map)")
	}
	parsed := chunked(joined, "pdf", "text-layer")
	parsed.Meta.Pages = len(pages)
	return parsed, nil
}

// printableText rejects glyph-index garbage: a text layer is useful when most
// of its non-space runes are letters, digits or ordinary punctuation.
func printableText(text string) bool {
	var total, suspicious float64
	for _, r := range text {
		if r == '\n' || r == '\t' || r == ' ' {
			continue
		}
		total++
		switch {
		case r >= 0xE000 && r <= 0xF8FF, r == 0xFFFD, r < 0x20:
			suspicious++
		}
	}
	if total == 0 {
		return false
	}
	return suspicious/total <= 0.4
}
