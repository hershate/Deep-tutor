package surfacespec

import (
	"net/url"
	"path"
	"strings"
)

type Image struct {
	Path   string `json:"path"`
	Alt    string `json:"alt"`
	Source string `json:"source,omitempty"`
}

func validImage(i *Image) bool {
	if i == nil {
		return true
	}
	if i.Source != "" {
		u, err := url.Parse(i.Source)
		if err != nil || len(i.Source) > 4096 || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || strings.ContainsAny(i.Source, "\\\r\n\t") {
			return false
		}
	}
	if i.Path == "" || len(i.Path) > 1024 || i.Alt == "" || len(i.Alt) > MaxLabel || strings.ContainsAny(i.Path, ":\\?#%\x00\r\n\t") {
		return false
	}
	for segment := range strings.SplitSeq(i.Path, "/") {
		if segment == "" || segment == "." || segment == ".." {
			return false
		}
	}
	switch strings.ToLower(path.Ext(i.Path)) {
	case ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif":
		return true
	default:
		return false
	}
}
