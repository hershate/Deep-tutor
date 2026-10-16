package builtin

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	_ "golang.org/x/image/webp"
	nethtml "golang.org/x/net/html"
	"reasonix/internal/base/netclient"
	"reasonix/internal/contract/tool"
)

type webImage struct {
	workDir   string
	roots     []string
	guard     SessionDataGuard
	proxySpec netclient.ProxySpec
	receipt   func(string, bool, []byte)
}

func init()                             { tool.RegisterBuiltin(webImage{}) }
func (webImage) Name() string           { return "import_web_image" }
func (webImage) ReadOnly() bool         { return false }
func (webImage) WritesNamedPaths() bool { return true }
func (webImage) Description() string {
	return "Import web_fetch image_url from source url into new path. Validates PNG/JPEG/GIF/WebP. Returns image {path,alt,source} for render_surface. Match extension."
}
func (webImage) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","additionalProperties":false,"required":["url","image_url","path","alt"],"properties":{"url":{"type":"string"},"image_url":{"type":"string"},"path":{"type":"string"},"alt":{"type":"string"}}}`)
}
func (webImage) Provenance(args json.RawMessage) tool.Provenance {
	return (webFetch{}).Provenance(args)
}

type imageCandidate struct {
	URL string `json:"url"`
	Alt string `json:"alt,omitempty"`
}

func webImageURL(raw string, base *url.URL) *url.URL {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return nil
	}
	if base != nil {
		u = base.ResolveReference(u)
	}
	if (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return nil
	}
	u.Fragment = ""
	return u
}

func pageImages(body []byte, base *url.URL) []imageCandidate {
	var out []imageCandidate
	base = pageImageBase(body, base)
	seen := map[string]bool{}
	z := nethtml.NewTokenizer(bytes.NewReader(body))
	for len(out) < 32 {
		tt := z.Next()
		if tt == nethtml.ErrorToken {
			break
		}
		if tt != nethtml.StartTagToken && tt != nethtml.SelfClosingTagToken {
			continue
		}
		token := z.Token()
		attrs := map[string]string{}
		for _, a := range token.Attr {
			attrs[a.Key] = a.Val
		}
		var sources []string
		alt := attrs["alt"]
		switch token.Data {
		case "img":
			sources = []string{attrs["src"], attrs["data-src"]}
		case "meta":
			key := attrs["property"]
			if key == "" {
				key = attrs["name"]
			}
			if key == "og:image" || key == "og:image:url" || key == "twitter:image" {
				sources = []string{attrs["content"]}
			}
		}

		for _, raw := range sources {
			if raw == "" {
				continue
			}
			u := webImageURL(raw, base)
			if u == nil || len(u.String()) > 4096 || seen[u.String()] {
				continue
			}
			if len(alt) > 200 {
				alt = ""
			}
			seen[u.String()] = true
			out = append(out, imageCandidate{URL: u.String(), Alt: alt})
			if len(out) == 32 {
				return out
			}
		}
	}
	return out
}

func pageImageBase(body []byte, page *url.URL) *url.URL {
	z := nethtml.NewTokenizer(bytes.NewReader(body))
	for {
		kind := z.Next()
		if kind == nethtml.ErrorToken {
			return page
		}
		if kind != nethtml.StartTagToken && kind != nethtml.SelfClosingTagToken {
			continue
		}
		token := z.Token()
		if token.Data != "base" {
			continue
		}
		for _, attr := range token.Attr {
			if attr.Key != "href" {
				continue
			}
			if base := webImageURL(attr.Val, page); base != nil {
				return base
			}
			return page
		}
	}
}

func (w webImage) fetch(ctx context.Context, raw string, limit int64) ([]byte, *url.URL, error) {
	u := webImageURL(raw, nil)
	if u == nil {
		return nil, nil, fmt.Errorf("absolute HTTP(S) URL without credentials required")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("User-Agent", "reasonix-web-fetch/1.0")
	resp, err := ssrfGuardedClient((webFetch{proxySpec: w.proxySpec}).proxyURLFor).Do(req)
	if err != nil {
		return nil, nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, nil, fmt.Errorf("image source returned HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil {
		return nil, nil, err
	}
	if int64(len(body)) > limit {
		return nil, nil, fmt.Errorf("image source exceeds %d bytes", limit)
	}
	if webImageURL(resp.Request.URL.String(), nil) == nil {
		return nil, nil, fmt.Errorf("invalid redirect URL")
	}
	return body, resp.Request.URL, nil
}

func (w webImage) Execute(ctx context.Context, args json.RawMessage) (string, error) {
	var p struct {
		URL      string `json:"url"`
		ImageURL string `json:"image_url"`
		Path     string `json:"path"`
		Alt      string `json:"alt"`
	}
	if err := json.Unmarshal(args, &p); err != nil {
		return "", err
	}
	if err := validateImageTarget(p.Path, p.Alt); err != nil {
		return "", err
	}
	target := resolveIn(w.workDir, p.Path)
	if err := confineWrite(ctx, w.roots, w.guard, ManagedConfigPaths{}, nil, target); err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(ctx, 2*webFetchTimeout)
	defer cancel()
	page, source, err := w.fetch(ctx, p.URL, webFetchMaxRead)
	if err != nil {
		return "", err
	}
	found := false
	for _, candidate := range pageImages(page, source) {
		if candidate.URL == p.ImageURL {
			found = true
			break
		}
	}
	if !found {
		return "", fmt.Errorf("image_url is not an observed image on the source page")
	}
	data, resolved, err := w.fetch(ctx, p.ImageURL, 8<<20)
	if err != nil {
		return "", err
	}
	cfg, format, err := image.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return "", fmt.Errorf("unsupported or invalid raster image: %w", err)
	}
	if cfg.Width < 1 || cfg.Height < 1 || int64(cfg.Width)*int64(cfg.Height) > 40_000_000 {
		return "", fmt.Errorf("image dimensions exceed limit")
	}
	ext := strings.ToLower(filepath.Ext(p.Path))
	if ext == ".jpg" {
		ext = ".jpeg"
	}
	if ext != "."+format {
		return "", fmt.Errorf("path extension must match %s image", format)
	}
	if _, _, err = image.Decode(bytes.NewReader(data)); err != nil {
		return "", fmt.Errorf("invalid raster image: %w", err)
	}
	dir := w.workDir
	if dir == "" {
		dir = "."
	}
	root, err := os.OpenRoot(dir)
	if err != nil {
		return "", err
	}
	defer root.Close()
	if err = root.MkdirAll(filepath.Dir(p.Path), 0755); err != nil {
		return "", err
	}
	file, err := root.OpenFile(p.Path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return "", err
	}
	_, writeErr := file.Write(data)
	closeErr := file.Close()
	if writeErr != nil || closeErr != nil {
		_ = root.Remove(p.Path)
		return "", fmt.Errorf("save image: %w", errors.Join(writeErr, closeErr))
	}
	if w.receipt != nil {
		w.receipt(target, false, nil)
	}
	result := map[string]any{"image": map[string]string{"path": p.Path, "alt": p.Alt, "source": source.String()}, "image_url": resolved.String(), "sha256": fmt.Sprintf("%x", sha256.Sum256(data)), "width": cfg.Width, "height": cfg.Height}
	out, err := json.Marshal(result)
	return string(out), err
}

func validateImageTarget(path, alt string) error {
	if alt == "" || len(alt) > 200 || len(path) > 1024 || strings.ContainsAny(path, ":\\?#%\x00\r\n\t") || !filepath.IsLocal(path) {
		return fmt.Errorf("valid relative image path and alt required")
	}
	for segment := range strings.SplitSeq(path, "/") {
		if segment == "" || segment == "." || segment == ".." {
			return fmt.Errorf("invalid image path")
		}
	}
	return nil
}
