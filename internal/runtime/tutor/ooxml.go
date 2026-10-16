package tutor

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/url"
	"path"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// A docx, pptx and epub are zip containers whose payloads are XML. The guards
// below mirror Deep-tutor's R23: a compressed archive can unpack to
// gigabytes, so the central directory is inspected before any entry is read.
const (
	zipMaxUncompressed = 512 << 20
	zipMaxEntries      = 10000
)

var errZipBomb = errors.New("tutor: archive exceeds unzip budget")

// openZipGuarded opens a container after checking entry count and total
// uncompressed size from the directory, never the payload.
func openZipGuarded(data []byte) (*zip.Reader, error) {
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, fmt.Errorf("tutor: not a valid zip container: %w", err)
	}
	if len(reader.File) > zipMaxEntries {
		return nil, fmt.Errorf("%w: %d entries", errZipBomb, len(reader.File))
	}
	var total uint64
	for _, f := range reader.File {
		total += f.UncompressedSize64
		if total > zipMaxUncompressed {
			return nil, fmt.Errorf("%w: payload over %dMB", errZipBomb, zipMaxUncompressed>>20)
		}
	}
	return reader, nil
}

// readZipEntry reads one entry under the unzip budget. The buffer is sized
// from the directory so an honest entry is copied once; an entry that
// streams past what it declared still lands on the budget error.
func readZipEntry(z *zip.Reader, name string) ([]byte, error) {
	var file *zip.File
	for _, f := range z.File {
		if f.Name == name {
			file = f
			break
		}
	}
	if file == nil {
		return nil, &fs.PathError{Op: "open", Path: name, Err: fs.ErrNotExist}
	}
	r, err := file.Open()
	if err != nil {
		return nil, err
	}
	defer r.Close()
	hint := min(file.UncompressedSize64, zipMaxUncompressed)
	data := make([]byte, hint)
	n, err := io.ReadFull(r, data)
	if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, err
	}
	rest, err := io.ReadAll(io.LimitReader(r, zipMaxUncompressed))
	if err != nil {
		return nil, err
	}
	if n+len(rest) > zipMaxUncompressed {
		return nil, fmt.Errorf("%w: entry %s streams past its declared size", errZipBomb, name)
	}
	return append(data[:n], rest...), nil
}

// xmlParagraphs walks an XML token stream and returns, in document order, one
// string per `para` element: the concatenated character data of its `text`
// children. python-docx and python-pptx build their text the same way, run by
// run, so a paragraph split across runs reassembles here.
func xmlParagraphs(decoder *xml.Decoder, para, text string) []string {
	var out []string
	var buf strings.Builder
	depth := 0
	for {
		tok, err := decoder.Token()
		if err != nil {
			return out
		}
		switch t := tok.(type) {
		case xml.StartElement:
			switch t.Name.Local {
			case para:
				depth++
				buf.Reset()
			case text:
				if depth > 0 {
					ch, err := decoder.Token()
					if ch, ok := ch.(xml.CharData); ok && err == nil {
						buf.Write(ch)
					}
				}
			}
		case xml.EndElement:
			if t.Name.Local == para && depth > 0 {
				depth--
				out = append(out, buf.String())
			}
		}
	}
}

// parseDocx extracts word/document.xml paragraphs, python-docx's
// Document.paragraphs equivalent.
func parseDocx(data []byte) (*ParsedMaterial, error) {
	z, err := openZipGuarded(data)
	if err != nil {
		return nil, fmt.Errorf("%w: docx: %w", ErrUnsupportedFormat, err)
	}
	doc, err := readZipEntry(z, "word/document.xml")
	if err != nil {
		return nil, fmt.Errorf("%w: docx without word/document.xml", ErrUnsupportedFormat)
	}
	paragraphs := xmlParagraphs(xml.NewDecoder(bytes.NewReader(doc)), "p", "t")
	kept := paragraphs[:0]
	for _, p := range paragraphs {
		if strings.TrimSpace(p) != "" {
			kept = append(kept, p)
		}
	}
	if len(kept) == 0 {
		return nil, errors.New("tutor: docx has no readable text")
	}
	parsed := chunked(strings.Join(kept, "\n"), "docx", "docx")
	parsed.Meta.Pages = 1
	return parsed, nil
}

var slideEntry = regexp.MustCompile(`^ppt/slides/slide(\d+)\.xml$`)

// parsePptx extracts slide text in numeric order: every paragraph of every
// shape's text frame, python-pptx's shape.text_frame walk.
func parsePptx(data []byte) (*ParsedMaterial, error) {
	z, err := openZipGuarded(data)
	if err != nil {
		return nil, fmt.Errorf("%w: pptx: %w", ErrUnsupportedFormat, err)
	}
	var numbers []int
	byNumber := map[int][]byte{}
	for _, f := range z.File {
		m := slideEntry.FindStringSubmatch(f.Name)
		if m == nil {
			continue
		}
		n, err := strconv.Atoi(m[1])
		if err != nil {
			continue
		}
		body, err := f.Open()
		if err != nil {
			continue
		}
		sl, err := io.ReadAll(io.LimitReader(body, zipMaxUncompressed))
		body.Close()
		if err != nil {
			continue
		}
		numbers = append(numbers, n)
		byNumber[n] = sl
	}
	if len(numbers) == 0 {
		return nil, fmt.Errorf("%w: pptx without slides", ErrUnsupportedFormat)
	}
	sort.Ints(numbers)
	var lines []string
	for _, n := range numbers {
		for _, p := range xmlParagraphs(xml.NewDecoder(bytes.NewReader(byNumber[n])), "p", "t") {
			if strings.TrimSpace(p) != "" {
				lines = append(lines, p)
			}
		}
	}
	if len(lines) == 0 {
		return nil, errors.New("tutor: pptx slides have no readable text")
	}
	parsed := chunked(strings.Join(lines, "\n"), "pptx", "pptx")
	parsed.Meta.Pages = len(numbers)
	return parsed, nil
}

// parseEpub follows the epub spine: container.xml names the OPF, the OPF's
// manifest maps id to href and its spine fixes the reading order; each XHTML
// document is reduced to visible text.
func parseEpub(data []byte) (*ParsedMaterial, error) {
	z, err := openZipGuarded(data)
	if err != nil {
		return nil, fmt.Errorf("%w: epub: %w", ErrUnsupportedFormat, err)
	}
	if mime, err := readZipEntry(z, "mimetype"); err != nil || strings.TrimSpace(string(mime)) != "application/epub+zip" {
		return nil, fmt.Errorf("%w: epub missing application/epub+zip marker", ErrUnsupportedFormat)
	}
	container, err := readZipEntry(z, "META-INF/container.xml")
	if err != nil {
		return nil, fmt.Errorf("%w: epub without META-INF/container.xml", ErrUnsupportedFormat)
	}
	opfPath, err := epubRootfile(container)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnsupportedFormat, err)
	}
	opf, err := readZipEntry(z, opfPath)
	if err != nil {
		return nil, fmt.Errorf("%w: epub package %s unreadable", ErrUnsupportedFormat, opfPath)
	}
	hrefs, order, err := epubSpine(opf)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrUnsupportedFormat, err)
	}
	base := path.Dir(opfPath)
	// Chapters are independent documents; the zip base is a bytes.Reader,
	// whose ReadAt backing is safe to read concurrently, so each worker
	// inflates and parses its own chapter. Slots keep the spine order.
	bodies := make([]string, len(order))
	parallelFor(len(order), func(i int) {
		href, ok := hrefs[order[i]]
		if !ok {
			return
		}
		doc, err := readZipEntry(z, epubResolve(base, href))
		if err != nil {
			return
		}
		_, body := htmlToText(doc)
		if strings.TrimSpace(body) != "" {
			bodies[i] = body
		}
	})
	var texts []string
	for _, body := range bodies {
		if body != "" {
			texts = append(texts, body)
		}
	}
	if len(texts) == 0 {
		return nil, errors.New("tutor: epub spine has no readable text")
	}
	return chunked(strings.Join(texts, "\n\n"), "epub", "spine"), nil
}

// epubRootfile reads container.xml for the default rootfile's full-path.
func epubRootfile(container []byte) (string, error) {
	decoder := xml.NewDecoder(bytes.NewReader(container))
	for {
		tok, err := decoder.Token()
		if err != nil {
			return "", errors.New("container.xml names no rootfile")
		}
		start, ok := tok.(xml.StartElement)
		if !ok || start.Name.Local != "rootfile" {
			continue
		}
		for _, a := range start.Attr {
			if a.Name.Local == "full-path" && a.Value != "" {
				return a.Value, nil
			}
		}
	}
}

// epubSpine reads the OPF: manifest id→href plus the spine's idref order.
func epubSpine(opf []byte) (map[string]string, []string, error) {
	hrefs := map[string]string{}
	var order []string
	inManifest, inSpine := false, false
	decoder := xml.NewDecoder(bytes.NewReader(opf))
	for {
		tok, err := decoder.Token()
		if err != nil {
			break
		}
		switch t := tok.(type) {
		case xml.StartElement:
			switch t.Name.Local {
			case "manifest":
				inManifest = true
			case "spine":
				inSpine = true
			case "item":
				if !inManifest {
					continue
				}
				var id, href, media string
				for _, a := range t.Attr {
					switch a.Name.Local {
					case "id":
						id = a.Value
					case "href":
						href = a.Value
					case "media-type":
						media = a.Value
					}
				}
				if id != "" && (media == "application/xhtml+xml" || media == "text/html") {
					hrefs[id] = href
				}
			case "itemref":
				if inSpine {
					for _, a := range t.Attr {
						if a.Name.Local == "idref" {
							order = append(order, a.Value)
						}
					}
				}
			}
		case xml.EndElement:
			switch t.Name.Local {
			case "manifest":
				inManifest = false
			case "spine":
				inSpine = false
			}
		}
	}
	if len(hrefs) == 0 {
		return nil, nil, errors.New("package manifest is empty")
	}
	return hrefs, order, nil
}

// epubResolve joins an OPF-relative href onto the OPF's directory,
// percent-decoding the href as the epub spec requires.
func epubResolve(base, href string) string {
	if decoded, err := url.PathUnescape(href); err == nil {
		href = decoded
	}
	if base == "." || base == "" {
		return path.Clean(href)
	}
	return path.Clean(path.Join(base, href))
}
