package tutor

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"

	"golang.org/x/net/html"
)

const (
	// MaxParsedChars caps one material's parsed text, mirroring Deep-tutor.
	MaxParsedChars = 500_000
	chunkSize      = 3000
	urlTimeout     = 30 * time.Second
	maxURLBytes    = 10 << 20
	maxRedirects   = 5
	promptTextCap  = 20_000
)

// ErrUnsupportedFormat marks an input whose parser is not available; the
// caller surfaces it instead of silently parsing nothing.
var ErrUnsupportedFormat = errors.New("tutor: unsupported material format")

// ParsedMaterial is parse_material's output: full text plus chunk slices for
// downstream summarization.
type ParsedMaterial struct {
	Text   string    `json:"text"`
	Chunks []string  `json:"chunks"`
	Meta   ParseMeta `json:"meta"`
}

type ParseMeta struct {
	SourceType string `json:"source_type"`
	Parser     string `json:"parser"`
	Chars      int    `json:"chars"`
	Pages      int    `json:"pages,omitempty"`
}

type MaterialInput struct {
	Kind   MaterialKind
	Source string // filename or URL
	Text   string // kind=text
	Data   []byte // raw file bytes
}

// ParseMaterial dispatches on kind and extension. Text, markdown, docx,
// pptx, epub and pdf text layers parse natively; URLs fetch with an SSRF
// guard and HTML extraction; media transcription needs optional binaries
// Reasonix does not bundle.
func ParseMaterial(ctx context.Context, in MaterialInput) (*ParsedMaterial, error) {
	switch in.Kind {
	case MaterialText:
		return chunked(in.Text, "text", "raw"), nil
	case MaterialURL:
		return fetchURLMaterial(ctx, in.Source)
	case MaterialFile:
		return parseFile(in)
	case MaterialImage, MaterialAudio, MaterialVideo:
		return nil, fmt.Errorf("%w: %s needs optional media dependencies", ErrUnsupportedFormat, in.Kind)
	default:
		return nil, fmt.Errorf("%w: kind %q", ErrUnsupportedFormat, in.Kind)
	}
}

func parseFile(in MaterialInput) (*ParsedMaterial, error) {
	ext := strings.ToLower(strings.TrimPrefix(path.Ext(in.Source), "."))
	switch ext {
	case "txt", "md", "markdown", "text":
		return chunked(string(in.Data), "file", ext), nil
	case "docx":
		return parseDocx(in.Data)
	case "pptx":
		return parsePptx(in.Data)
	case "epub":
		return parseEpub(in.Data)
	case "pdf":
		return parsePDF(in.Data)
	default:
		return nil, fmt.Errorf("%w: .%s", ErrUnsupportedFormat, ext)
	}
}

func chunked(text, sourceType, parser string) *ParsedMaterial {
	// One rune conversion serves the cap check, the truncation, and the
	// chunk slicing; converting per step rewalked the whole document.
	runes := []rune(text)
	if len(runes) > MaxParsedChars {
		runes = runes[:MaxParsedChars]
		text = string(runes) + "\n…（超出长度上限已截断）"
	}
	chunks := make([]string, 0, len(runes)/chunkSize+1)
	for start := 0; start < len(runes); start += chunkSize {
		end := min(start+chunkSize, len(runes))
		chunks = append(chunks, string(runes[start:end]))
	}
	return &ParsedMaterial{
		Text:   text,
		Chunks: chunks,
		Meta:   ParseMeta{SourceType: sourceType, Parser: parser, Chars: len(runes)},
	}
}

func fetchURLMaterial(ctx context.Context, rawURL string) (*ParsedMaterial, error) {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return nil, fmt.Errorf("tutor: invalid url %q", rawURL)
	}
	client := publicOnlyClient(urlTimeout)
	client.CheckRedirect = guardedRedirect
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "reasonix-tutor/1.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxURLBytes+1))
	if err != nil {
		return nil, err
	}
	if len(body) > maxURLBytes {
		return nil, errors.New("tutor: url body exceeds 10MB cap")
	}
	ctype := resp.Header.Get("Content-Type")
	if strings.Contains(ctype, "text/html") {
		title, text := htmlToText(body)
		if title != "" {
			text = "# " + title + "\n\n" + text
		}
		return chunked(text, "url", "html"), nil
	}
	return chunked(string(body), "url", "raw"), nil
}

// guardedRedirect caps redirect hops and refuses redirects whose host
// resolves into private address space.
func guardedRedirect(req *http.Request, via []*http.Request) error {
	if len(via) >= maxRedirects {
		return errors.New("tutor: too many redirects")
	}
	return requirePublicHost(req.URL)
}

func requirePublicHost(u *url.URL) error {
	host := u.Hostname()
	if host == "" {
		return errors.New("tutor: empty host")
	}
	if ip := net.ParseIP(host); ip != nil {
		if !ip.IsGlobalUnicast() || isPrivateIP(ip) {
			return errors.New("tutor: host resolves to private address")
		}
		return nil
	}
	addrs, err := net.LookupIP(host)
	if err != nil {
		return err
	}
	for _, ip := range addrs {
		if !ip.IsGlobalUnicast() || isPrivateIP(ip) {
			return errors.New("tutor: host resolves to private address")
		}
	}
	return nil
}

func isPrivateIP(ip net.IP) bool {
	return ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified()
}

// htmlToText extracts visible text: script/style drop, block elements break
// lines, anchors keep their href when the label differs.
func htmlToText(data []byte) (title, text string) {
	doc, err := html.Parse(bytes.NewReader(data))
	if err != nil {
		return "", string(data)
	}
	var sb strings.Builder
	// One grow at the input's size: the walk's piecewise writes otherwise
	// double-copy the whole document through the builder's growth chain.
	sb.Grow(len(data))
	var walk func(n *html.Node)
	walk = func(n *html.Node) {
		switch n.Type {
		case html.ElementNode:
			switch n.Data {
			case "script", "style", "noscript", "template":
				return
			case "title":
				if n.FirstChild != nil {
					title = strings.TrimSpace(n.FirstChild.Data)
				}
				return
			}
		case html.TextNode:
			sb.WriteString(n.Data)
			sb.WriteString(" ")
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
		switch n.Type {
		case html.ElementNode:
			switch n.Data {
			case "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article":
				sb.WriteString("\n")
			}
		}
	}
	walk(doc)
	return title, strings.TrimSpace(sb.String())
}

// truncateForPrompt bounds a text blob handed to a model turn; the marker
// keeps the model from treating a cut as complete.
func truncateForPrompt(s string) string {
	runes := []rune(s)
	if len(runes) <= promptTextCap {
		return s
	}
	return string(runes[:promptTextCap]) + "\n…（已截断）"
}
