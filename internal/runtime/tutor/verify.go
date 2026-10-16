package tutor

import (
	"context"
	"errors"
	"fmt"
	"html"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"
)

// verify.go ports Deep-tutor's verify_url: hop-by-hop redirects through the
// SSRF guard, robots.txt honoured, a 16KB body prefix, login-wall flags, and
// TTL-cached verdicts.

const (
	verifyTimeout    = 30 * time.Second
	verifyMaxHops    = 5
	verifyPrefixCap  = 16 << 10
	verifyMinBody    = 512
	robotsTimeout    = 5 * time.Second
	verifyUserAgent  = "Reasonix-Tutor/1.0 (learning-plan verifier)"
	snippetMaxRunes  = 400
	verifyCacheOK    = 24 * time.Hour
	verifyCacheErr   = time.Hour
	verifyCacheMax   = 4096
	loginWallKeyword = "captcha|recaptcha|hcaptcha|cf-challenge|challenge-platform" +
		"|sign in to continue|log in to continue|请登录|登录后继续|登录以继续|验证您是真人|安全验证"
)

var metadataHosts = map[string]bool{
	"169.254.169.254":          true,
	"169.254.170.2":            true,
	"metadata.google.internal": true,
}

var loginWallRe = regexp.MustCompile(`(?i)` + loginWallKeyword)

// verifyVerdict is one checked URL: the label a plan carries plus the body
// prefix the screening gate reads.
type verifyVerdict struct {
	Label    string `json:"label"` // ok | uncertain | invalid
	FinalURL string `json:"final_url"`
	Reason   string `json:"reason,omitempty"`
	Snippet  string `json:"snippet,omitempty"`
}

// verifyCache is the process-wide TTL cache, FIFO-bounded like Deep-tutor's.
type verifyCache struct {
	mu    sync.Mutex
	items map[string]verifyCacheEntry
	order []string
	now   func() time.Time
}

type verifyCacheEntry struct {
	expires time.Time
	verdict verifyVerdict
}

var sharedVerifyCache = &verifyCache{items: map[string]verifyCacheEntry{}, now: time.Now}

func (c *verifyCache) get(rawURL string) (verifyVerdict, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.items[rawURL]
	if !ok {
		return verifyVerdict{}, false
	}
	if c.now().After(entry.expires) {
		delete(c.items, rawURL)
		return verifyVerdict{}, false
	}
	return entry.verdict, true
}

func (c *verifyCache) put(rawURL string, verdict verifyVerdict, okLabel bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, exists := c.items[rawURL]; !exists {
		c.order = append(c.order, rawURL)
		if len(c.order) > verifyCacheMax {
			delete(c.items, c.order[0])
			c.order = c.order[1:]
		}
	}
	ttl := verifyCacheErr
	if okLabel {
		ttl = verifyCacheOK
	}
	c.items[rawURL] = verifyCacheEntry{expires: c.now().Add(ttl), verdict: verdict}
}

// robotsCache remembers per-host robots verdicts for an hour.
type robotsCache struct {
	mu    sync.Mutex
	items map[string]robotsEntry
	order []string
	now   func() time.Time
}

type robotsEntry struct {
	expires time.Time
	allowed bool
}

var sharedRobotsCache = &robotsCache{items: map[string]robotsEntry{}, now: time.Now}

func (c *robotsCache) get(host string) (bool, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.items[host]
	if !ok {
		return false, false
	}
	if c.now().After(entry.expires) {
		delete(c.items, host)
		return false, false
	}
	return entry.allowed, true
}

func (c *robotsCache) put(host string, allowed bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, exists := c.items[host]; !exists {
		c.order = append(c.order, host)
		if len(c.order) > verifyCacheMax {
			delete(c.items, c.order[0])
			c.order = c.order[1:]
		}
	}
	c.items[host] = robotsEntry{expires: c.now().Add(time.Hour), allowed: allowed}
}

// verifyGuardedFetch hops through redirects manually so every target passes
// the SSRF guard, and reads at most a body prefix.
func verifyGuardedFetch(ctx context.Context, client *http.Client, rawURL string, guard func(string) error) (status int, finalURL string, prefix []byte, err error) {
	current := rawURL
	for hop := 0; hop <= verifyMaxHops; hop++ {
		if err := guard(current); err != nil {
			return 0, "", nil, err
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, current, nil)
		if err != nil {
			return 0, "", nil, err
		}
		req.Header.Set("User-Agent", verifyUserAgent)
		resp, err := client.Do(req)
		if err != nil {
			return 0, "", nil, err
		}
		if resp.StatusCode >= 301 && resp.StatusCode <= 308 && resp.Header.Get("Location") != "" {
			location := resp.Header.Get("Location")
			resp.Body.Close()
			next, err := url.Parse(location)
			if err != nil {
				return 0, "", nil, err
			}
			base, _ := url.Parse(current)
			current = base.ResolveReference(next).String()
			continue
		}
		defer resp.Body.Close()
		if resp.StatusCode == http.StatusOK {
			prefix, err = io.ReadAll(io.LimitReader(resp.Body, verifyPrefixCap))
			if err != nil {
				return resp.StatusCode, current, nil, err
			}
		}
		return resp.StatusCode, current, prefix, nil
	}
	return 0, "", nil, errors.New("tutor: too many redirects")
}

// requirePublicURL extends the material fetch guard with cloud metadata hosts.
func requirePublicURL(rawURL string) error {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return fmt.Errorf("tutor: invalid url")
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("tutor: url must be http(s)")
	}
	if metadataHosts[u.Hostname()] {
		return fmt.Errorf("tutor: cloud metadata endpoint")
	}
	return requirePublicHost(u)
}

// cgnatBlock is RFC 6598 shared address space (100.64.0.0/10); some clouds
// host instance metadata there, so it is refused like private space — the
// same addition web_fetch and install_source carry.
var cgnatBlock = func() *net.IPNet {
	_, n, err := net.ParseCIDR("100.64.0.0/10")
	if err != nil {
		panic(err)
	}
	return n
}()

// requirePublicIP is the tutor's blocked set at dial time: Reasonix's
// private/link-local/CGNAT refusals plus loopback, since a citation crawler
// has no business on the local network at all.
func requirePublicIP(ip net.IP) error {
	if isPrivateIP(ip) || cgnatBlock.Contains(ip) {
		return errors.New("tutor: internal address refused")
	}
	return nil
}

// publicOnlyDial applies Reasonix's dial-time SSRF scheme — the vetted-IP
// dial web_fetch and install_source use — under the tutor's stricter policy.
// Resolving at dial time and dialing the vetted IP closes the DNS-rebinding
// gap a check-then-dial hostname guard leaves open.
func publicOnlyDial(inner func(ctx context.Context, network, addr string) (net.Conn, error)) func(context.Context, string, string) (net.Conn, error) {
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		host, port, err := net.SplitHostPort(addr)
		if err != nil {
			return nil, err
		}
		if ip := net.ParseIP(host); ip != nil {
			if err := requirePublicIP(ip); err != nil {
				return nil, err
			}
			return inner(ctx, network, addr)
		}
		ips, err := net.DefaultResolver.LookupIPAddr(ctx, host)
		if err != nil {
			return nil, err
		}
		if len(ips) == 0 {
			return nil, errors.New("tutor: host does not resolve")
		}
		for _, ip := range ips {
			if err := requirePublicIP(ip.IP); err != nil {
				return nil, fmt.Errorf("tutor: %s resolves to %s: %w", host, ip.IP, err)
			}
		}
		return inner(ctx, network, net.JoinHostPort(ips[0].IP.String(), port))
	}
}

// publicOnlyClient is an HTTP client whose every dial — redirects included —
// is vetted; the parse-time requirePublicURL checks stay as the first line
// with the friendlier errors.
func publicOnlyClient(timeout time.Duration) *http.Client {
	return &http.Client{
		Timeout:   timeout,
		Transport: &http.Transport{DialContext: publicOnlyDial((&net.Dialer{}).DialContext)},
	}
}

// robotsAllowed fetches and parses robots.txt for the URL's host; a missing
// or unreadable robots file permits the fetch.
func robotsAllowed(ctx context.Context, client *http.Client, rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil || u.Host == "" {
		return true
	}
	if cached, ok := sharedRobotsCache.get(u.Host); ok {
		return cached
	}
	allowed := true
	robotsCtx, cancel := context.WithTimeout(ctx, robotsTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(robotsCtx, http.MethodGet, u.Scheme+"://"+u.Host+"/robots.txt", nil)
	if err == nil {
		req.Header.Set("User-Agent", verifyUserAgent)
		if resp, err := client.Do(req); err == nil {
			defer resp.Body.Close()
			if resp.StatusCode == http.StatusOK {
				body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
				allowed = robotsPermits(body, u.Path)
			}
		}
	}
	sharedRobotsCache.put(u.Host, allowed)
	return allowed
}

// robotsRule is one Allow/Disallow prefix with its group.
type robotsRule struct {
	path     string
	disallow bool
}

// robotsPermits reads robots.txt groups conservatively: rules from both the
// "*" group and any group naming this agent apply, and the longest matching
// rule decides. Over-binding beats accidentally crawling a forbidden path.
func robotsPermits(body []byte, path string) bool {
	type group struct {
		agents []string
		rules  []robotsRule
	}
	var groups []*group
	var current *group
	agentLine := true
	for line := range strings.SplitSeq(string(body), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, found := strings.Cut(line, ":")
		if !found {
			continue
		}
		key = strings.ToLower(strings.TrimSpace(key))
		value = strings.TrimSpace(value)
		switch key {
		case "user-agent":
			if !agentLine {
				current = nil
			}
			agentLine = true
			if current == nil {
				current = &group{}
				groups = append(groups, current)
			}
			current.agents = append(current.agents, strings.ToLower(value))
		case "disallow", "allow":
			agentLine = false
			if current == nil {
				continue
			}
			current.rules = append(current.rules, robotsRule{path: value, disallow: key == "disallow"})
		default:
			agentLine = false
		}
	}
	var applicable []robotsRule
	for _, g := range groups {
		mine := false
		for _, agent := range g.agents {
			if agent == "*" || strings.Contains(agent, "reasonix") {
				mine = true
			}
		}
		if mine {
			applicable = append(applicable, g.rules...)
		}
	}
	best := -1
	verdict := true
	for _, rule := range applicable {
		if rule.path == "" {
			continue
		}
		if !robotsMatch(rule.path, path) {
			continue
		}
		if len(rule.path) > best {
			best = len(rule.path)
			verdict = !rule.disallow
		}
	}
	return verdict
}

func robotsMatch(rule, path string) bool {
	if rule == "/" {
		return true
	}
	return strings.HasPrefix(path, rule)
}

// snippetFromPrefix distils the body prefix for the screening gate.
func snippetFromPrefix(prefix []byte) string {
	text := strings.Map(func(r rune) rune {
		if r == '\n' || r == '\r' || r == '\t' {
			return ' '
		}
		return r
	}, string(prefix))
	text = stripTags(text)
	text = html.UnescapeString(text)
	text = strings.Join(strings.Fields(text), " ")
	runes := []rune(text)
	if len(runes) > snippetMaxRunes {
		runes = runes[:snippetMaxRunes]
	}
	return string(runes)
}

var anyTag = regexp.MustCompile(`<[^>]*>`)

func stripTags(text string) string {
	return anyTag.ReplaceAllString(text, " ")
}

// analyzeBody flags a 200 whose prefix smells like a login wall or an empty
// shell — reachable, but not a citation.
func analyzeBody(prefix []byte) string {
	if len(strings.TrimSpace(string(prefix))) < verifyMinBody {
		return "content_too_short"
	}
	if match := loginWallRe.FindString(strings.ToLower(string(prefix))); match != "" {
		return "login_wall:" + match
	}
	return ""
}

// verifyURL checks one candidate end to end, through the caches. The guard
// parameter lets a test admit loopback fixtures without weakening the
// production check.
func verifyURL(ctx context.Context, client *http.Client, rawURL string, guard func(string) error) verifyVerdict {
	if guard == nil {
		guard = requirePublicURL
	}
	if err := guard(rawURL); err != nil {
		return verifyVerdict{Label: "invalid", FinalURL: rawURL, Reason: "ssrf_blocked"}
	}
	if verdict, ok := sharedVerifyCache.get(rawURL); ok {
		return verdict
	}
	if !robotsAllowed(ctx, client, rawURL) {
		verdict := verifyVerdict{Label: "uncertain", FinalURL: rawURL, Reason: "robots_disallowed"}
		sharedVerifyCache.put(rawURL, verdict, false)
		return verdict
	}
	fetchCtx, cancel := context.WithTimeout(ctx, verifyTimeout)
	defer cancel()
	status, finalURL, prefix, err := verifyGuardedFetch(fetchCtx, client, rawURL, guard)
	if err != nil {
		verdict := verifyVerdict{Label: "invalid", FinalURL: rawURL}
		sharedVerifyCache.put(rawURL, verdict, false)
		return verdict
	}
	if status != http.StatusOK {
		verdict := verifyVerdict{Label: "invalid", FinalURL: finalURL, Reason: fmt.Sprintf("status_%d", status)}
		sharedVerifyCache.put(rawURL, verdict, false)
		return verdict
	}
	verdict := verifyVerdict{Label: "ok", FinalURL: finalURL}
	if reason := analyzeBody(prefix); reason != "" {
		verdict.Label = "uncertain"
		verdict.Reason = reason
	}
	verdict.Snippet = snippetFromPrefix(prefix)
	sharedVerifyCache.put(rawURL, verdict, verdict.Label == "ok")
	return verdict
}
