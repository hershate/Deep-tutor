package tutor

// slides_render.go renders a SlideSpec into one self-contained HTML file:
// Deep-tutor's ppt-slide-builder shell (themes, transitions, animations,
// navigation) with LLM-sourced markup scrubbed on the way in.

import (
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
)

// slidesShellOrder lists the shell's CSS variables in :root order; theme
// overrides only apply to these names (the template's own variable set).
var slidesShellOrder = []string{
	"--bg", "--bg-alt", "--text", "--text-secondary", "--accent", "--accent-hover",
	"--border", "--font-display", "--font-body", "--font-mono",
	"--slide-padding", "--content-max-width", "--transition-duration", "--transition-easing",
}

var slidesShellDefaults = map[string]string{
	"--bg":                  "#ffffff",
	"--bg-alt":              "#f8f9fa",
	"--text":                "#1a1a1a",
	"--text-secondary":      "#6b7280",
	"--accent":              "#3b82f6",
	"--accent-hover":        "#2563eb",
	"--border":              "#e5e7eb",
	"--font-display":        "'Georgia', 'Times New Roman', serif",
	"--font-body":           "system-ui, -apple-system, sans-serif",
	"--font-mono":           "'Courier New', monospace",
	"--slide-padding":       "4rem",
	"--content-max-width":   "960px",
	"--transition-duration": "0.6s",
	"--transition-easing":   "cubic-bezier(0.16, 1, 0.3, 1)",
}

// sanitizeThemeValue drops characters that could break out of the style
// block.
func sanitizeThemeValue(v string) string {
	return strings.Map(func(r rune) rune {
		switch r {
		case '<', '>', '{', '}':
			return -1
		}
		return r
	}, v)
}

var slidesClassUnsafe = regexp.MustCompile(`[^a-zA-Z0-9_-]`)

// sanitizeClass keeps only class-name characters.
func sanitizeClass(s string) string {
	return slidesClassUnsafe.ReplaceAllString(s, "")
}

var (
	// Whole dangerous blocks with their closing tag. RE2 has no
	// backreferences, so the port compiles one pattern per fixed tag name.
	slidesUnsafeBlocks = func() []*regexp.Regexp {
		var rs []*regexp.Regexp
		for _, tag := range []string{"script", "style", "iframe", "object", "embed", "link", "meta"} {
			rs = append(rs, regexp.MustCompile(`(?is)<\s*`+tag+`\b.*?<\s*/\s*`+tag+`\s*>`))
		}
		return rs
	}()
	slidesUnsafeTags   = regexp.MustCompile(`(?i)<\s*/?\s*(?:script|style|iframe|object|embed|link|meta)\b[^>]*>`)
	slidesEventAttrs   = regexp.MustCompile(`(?i)\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)`)
	slidesURLAttr      = regexp.MustCompile(`(?i)(href|src)\s*=\s*("([^"]*)"|'([^']*)'|[^\s>]+)`)
	slidesBadScheme    = regexp.MustCompile(`(?i)^(?:javascript|vbscript):`)
	slidesControlChars = regexp.MustCompile("[\x00-\x20\x7f]")
)

// sanitizeContentHTML scrubs LLM-generated markup: entity decoding happens
// first (a java&#115;cript: URL must be caught after unescaping), then
// dangerous blocks and tags, on* event attributes, and javascript: /
// vbscript: URLs (ASCII control characters stripped before the scheme test,
// because browsers strip them too).
func sanitizeContentHTML(s string) string {
	s = html.UnescapeString(s)
	for _, re := range slidesUnsafeBlocks {
		s = re.ReplaceAllString(s, "")
	}
	s = slidesUnsafeTags.ReplaceAllString(s, "")
	s = slidesEventAttrs.ReplaceAllString(s, "")
	return slidesURLAttr.ReplaceAllStringFunc(s, func(m string) string {
		groups := slidesURLAttr.FindStringSubmatch(m)
		val := groups[3]
		if val == "" && groups[4] != "" {
			val = groups[4]
		} else if val == "" {
			val = groups[5]
		}
		if slidesBadScheme.MatchString(slidesControlChars.ReplaceAllString(val, "")) {
			return groups[1] + `="#"`
		}
		return m
	})
}

// renderSlideSection renders one slide: the first is the active title page
// (h1); the rest are content pages (h2).
func renderSlideSection(idx int, sc SlideContent, first bool) string {
	tag, active, hidden := "h2", "", "true"
	if first {
		tag, active, hidden = "h1", " active", "false"
	}
	layout := sanitizeClass(sc.Layout)
	if layout == "" {
		layout = "left"
	}
	anim := sanitizeClass(sc.Animation)
	if anim == "" {
		anim = "anim-fade-in"
	}
	titleHTML := ""
	if sc.Title != "" {
		titleHTML = fmt.Sprintf("\n      <%s class=\"%s\">%s</%s>", tag, anim, slideEsc(sc.Title), tag)
	}
	content := sanitizeContentHTML(sc.ContentHTML)
	return fmt.Sprintf(
		"  <!-- SLIDE: slide-%d -->\n"+
			"  <section id=\"slide-%d\" class=\"slide%s\" data-slide=\"%d\" role=\"region\" aria-label=\"Slide %d\" aria-hidden=\"%s\">\n"+
			"    <div class=\"content-%s\">%s\n"+
			"      <div class=\"%s anim-delay-1\">%s</div>\n"+
			"    </div>\n"+
			"  </section>\n"+
			"  <!-- /SLIDE -->",
		idx, idx, active, idx, idx, hidden, layout, titleHTML, anim, content,
	)
}

func slidesRootCSS(theme map[string]string) string {
	var b strings.Builder
	b.WriteString(":root {\n")
	for _, name := range slidesShellOrder {
		val := slidesShellDefaults[name]
		if v, ok := theme[name]; ok {
			if clean := sanitizeThemeValue(v); clean != "" {
				val = clean
			}
		}
		fmt.Fprintf(&b, "  %s: %s;\n", name, val)
	}
	b.WriteString("  }")
	return b.String()
}

// RenderSlidesHTML assembles the deck. Empty decks render a lone title
// slide, so the shell's navigation never references missing slides.
func RenderSlidesHTML(spec SlideSpec) string {
	if len(spec.Slides) == 0 {
		spec.Slides = []SlideContent{{Title: titleOr(spec.Title, "学习方案"), Layout: "center"}}
	}
	total := len(spec.Slides)
	var sections strings.Builder
	for i, sc := range spec.Slides {
		if i > 0 {
			sections.WriteString("\n\n")
		}
		sections.WriteString(renderSlideSection(i+1, sc, i == 0))
	}
	presentation := `<div class="presentation" id="presentation">`
	if trans := sanitizeClass(spec.Transition); trans != "" {
		presentation = `<div class="presentation ` + trans + `" id="presentation">`
	}
	out := strings.Replace(slidesShell, "__ROOT_CSS__", slidesRootCSS(spec.Theme), 1)
	out = strings.Replace(out, "__PRESENTATION_OPEN__", presentation, 1)
	// strings.Replace treats the sections as literal text: slide markup with
	// backslashes or $ characters must never re-enter pattern processing.
	out = strings.Replace(out, "__SLIDES__", sections.String(), 1)
	out = strings.ReplaceAll(out, "__PRESENTATION_TITLE__", slideEsc(spec.Title))
	out = strings.ReplaceAll(out, "__SLIDE_COUNT__", strconv.Itoa(total))
	out = strings.Replace(out, "__PROGRESS_PERCENT__", strconv.FormatFloat(100/float64(total), 'f', 2, 64), 1)
	return out
}

// slidesShell is the self-contained presentation document: theme variables,
// slide typography and layouts, transition and animation classes, the
// navigation bar with dots and progress, reduced-motion and responsive
// rules, and the keyboard / wheel / click / touch navigation script.
const slidesShell = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__PRESENTATION_TITLE__</title>
<style>
/* ========== THEME ========== */
__ROOT_CSS__

/* ========== RESET ========== */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; font-family: var(--font-body); color: var(--text); background: var(--bg); -webkit-font-smoothing: antialiased; }

/* ========== PRESENTATION ========== */
.presentation { height: 100dvh; position: relative; overflow: hidden; }

/* ========== SLIDES ========== */
.slide {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  justify-content: center; align-items: center;
  padding: var(--slide-padding);
  opacity: 0; visibility: hidden;
  transition: opacity var(--transition-duration) var(--transition-easing), transform var(--transition-duration) var(--transition-easing);
  transform: translateY(24px) scale(0.98);
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.slide.active {
  opacity: 1; visibility: visible;
  transform: translateY(0) scale(1);
}
.slide > * { max-width: var(--content-max-width); width: 100%; }

/* Slide content typography */
.slide h1 { font-family: var(--font-display); font-size: clamp(2rem, 5vw, 4rem); line-height: 1.15; margin-bottom: 0.75em; }
.slide h2 { font-family: var(--font-display); font-size: clamp(1.5rem, 3.5vw, 2.5rem); line-height: 1.2; margin-bottom: 0.6em; }
.slide h3 { font-size: clamp(1.1rem, 2vw, 1.4rem); font-weight: 600; margin-bottom: 0.5em; }
.slide p { font-size: clamp(1rem, 1.8vw, 1.2rem); line-height: 1.7; margin-bottom: 1em; color: var(--text-secondary); }
.slide ul, .slide ol { padding-left: 1.5em; margin-bottom: 1em; }
.slide li { font-size: clamp(1rem, 1.8vw, 1.2rem); line-height: 1.7; margin-bottom: 0.4em; color: var(--text-secondary); }
.slide img { max-width: 100%; height: auto; border-radius: 8px; margin: 1em 0; }
.slide blockquote { border-left: 4px solid var(--accent); padding-left: 1.5em; margin: 1em 0; font-style: italic; color: var(--text-secondary); }
.slide code { font-family: var(--font-mono); font-size: 0.9em; background: var(--bg-alt); padding: 0.2em 0.4em; border-radius: 4px; }
.slide pre { background: var(--bg-alt); padding: 1.5em; border-radius: 8px; overflow-x: auto; margin: 1em 0; }
.slide pre code { background: none; padding: 0; }

/* Content layouts */
.slide .content-center { text-align: center; align-items: center; }
.slide .content-left { text-align: left; align-items: flex-start; }
.slide .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; margin: 1em 0; }
.slide .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1.5rem; margin: 1em 0; }
@media (max-width: 768px) {
  .slide .grid-2, .slide .grid-3 { grid-template-columns: 1fr; }
}

/* ========== SLIDE TRANSITIONS ========== */
.trans-fade .slide { transform: none; }
.trans-fade .slide.active { opacity: 1; }
.trans-slide-left .slide { transform: translateX(30px); }
.trans-slide-left .slide.active { transform: translateX(0); }
.trans-slide-right .slide { transform: translateX(-30px); }
.trans-slide-right .slide.active { transform: translateX(0); }
.trans-slide-up .slide { transform: translateY(30px); }
.trans-slide-up .slide.active { transform: translateY(0); }
.trans-zoom .slide { transform: scale(0.85); }
.trans-zoom .slide.active { transform: scale(1); }
.trans-flip { perspective: 1200px; }
.trans-flip .slide { transform: rotateX(10deg) translateY(20px); transform-origin: bottom; backface-visibility: hidden; }
.trans-flip .slide.active { transform: rotateX(0) translateY(0); }
.trans-none .slide { transform: none; transition: opacity 0.3s ease; }

/* ========== ELEMENT ANIMATIONS ========== */
.slide [class*="anim-"] { opacity: 0; }
.slide.active [class*="anim-"] { animation-fill-mode: both; animation-timing-function: var(--transition-easing); }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
.slide.active .anim-fade-in { animation-name: fadeIn; animation-duration: 0.6s; }
@keyframes slideUp { from { opacity: 0; transform: translateY(30px); } to { opacity: 1; transform: translateY(0); } }
.slide.active .anim-slide-up { animation-name: slideUp; animation-duration: 0.6s; }
@keyframes slideDown { from { opacity: 0; transform: translateY(-30px); } to { opacity: 1; transform: translateY(0); } }
.slide.active .anim-slide-down { animation-name: slideDown; animation-duration: 0.6s; }
@keyframes slideLeft { from { opacity: 0; transform: translateX(30px); } to { opacity: 1; transform: translateX(0); } }
.slide.active .anim-slide-left { animation-name: slideLeft; animation-duration: 0.6s; }
@keyframes slideRight { from { opacity: 0; transform: translateX(-30px); } to { opacity: 1; transform: translateX(0); } }
.slide.active .anim-slide-right { animation-name: slideRight; animation-duration: 0.6s; }
@keyframes scaleIn { from { opacity: 0; transform: scale(0.7); } to { opacity: 1; transform: scale(1); } }
.slide.active .anim-scale-in { animation-name: scaleIn; animation-duration: 0.5s; }
@keyframes rotateIn { from { opacity: 0; transform: rotate(-5deg) scale(0.9); } to { opacity: 1; transform: rotate(0) scale(1); } }
.slide.active .anim-rotate-in { animation-name: rotateIn; animation-duration: 0.6s; }
@keyframes blurIn { from { opacity: 0; filter: blur(10px); } to { opacity: 1; filter: blur(0); } }
.slide.active .anim-blur-in { animation-name: blurIn; animation-duration: 0.8s; }
@keyframes flipIn { from { opacity: 0; transform: perspective(600px) rotateX(-20deg); } to { opacity: 1; transform: perspective(600px) rotateX(0); } }
.slide.active .anim-flip-in { animation-name: flipIn; animation-duration: 0.7s; }
@keyframes bounceIn { 0% { opacity: 0; transform: translateY(40px); } 60% { transform: translateY(-8px); } 80% { transform: translateY(2px); } 100% { opacity: 1; transform: translateY(0); } }
.slide.active .anim-bounce-in { animation-name: bounceIn; animation-duration: 0.8s; }

/* Typewriter (for text only) */
.slide.active .anim-typewriter {
  overflow: hidden;
  white-space: nowrap;
  border-right: 2px solid var(--accent);
  width: 0;
  animation: typewriter 1.5s steps(30) forwards, blink 0.8s step-end infinite;
}
@keyframes typewriter { to { width: 100%; } }
@keyframes blink { 50% { border-color: transparent; } }

/* Highlight (background highlight sweep) */
@keyframes highlight { from { background-position: 200% center; } to { background-position: 0 center; } }
.slide.active .anim-highlight {
  background: linear-gradient(90deg, transparent 0, transparent 40%, rgba(59,130,246,0.15) 40%, rgba(59,130,246,0.15) 60%, transparent 60%, transparent 100%);
  background-size: 200% 100%;
  animation: highlight 0.8s var(--transition-easing) forwards;
}
.slide.active .anim-delay-1 { animation-delay: 0.1s; }
.slide.active .anim-delay-2 { animation-delay: 0.2s; }
.slide.active .anim-delay-3 { animation-delay: 0.3s; }
.slide.active .anim-delay-4 { animation-delay: 0.4s; }
.slide.active .anim-delay-5 { animation-delay: 0.5s; }
.slide.active .anim-delay-6 { animation-delay: 0.6s; }
.slide.active .anim-delay-7 { animation-delay: 0.7s; }
.slide.active .anim-delay-8 { animation-delay: 0.8s; }
.slide.active .anim-delay-9 { animation-delay: 0.9s; }
.slide.active .anim-delay-10 { animation-delay: 1.0s; }

/* ========== NAVIGATION ========== */
.slide-nav {
  position: fixed; bottom: 2rem; left: 50%; transform: translateX(-50%);
  display: flex; align-items: center; gap: 1rem;
  background: color-mix(in srgb, var(--bg) 90%, transparent);
  backdrop-filter: blur(8px);
  padding: 0.5rem 1rem; border-radius: 999px;
  box-shadow: 0 2px 12px rgba(0,0,0,0.08);
  border: 1px solid var(--border);
  z-index: 1000; user-select: none;
}
.nav-btn {
  width: 36px; height: 36px; border: none; border-radius: 50%;
  background: var(--bg-alt); color: var(--text); font-size: 1.1rem;
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  transition: background 0.2s, transform 0.2s;
}
.nav-btn:hover { background: var(--accent); color: white; transform: scale(1.05); }
.nav-btn:active { transform: scale(0.95); }
.nav-btn:disabled { opacity: 0.3; cursor: default; transform: none; }
.nav-btn:disabled:hover { background: var(--bg-alt); color: var(--text); }
.nav-dots { display: flex; gap: 6px; }
.nav-dot {
  width: 8px; height: 8px; border: none; border-radius: 50%;
  background: var(--border); cursor: pointer; padding: 0;
  transition: background 0.3s, transform 0.3s;
}
.nav-dot:hover { transform: scale(1.3); }
.nav-dot.active { background: var(--accent); transform: scale(1.2); }
.slide-counter { font-size: 0.8rem; color: var(--text-secondary); font-variant-numeric: tabular-nums; min-width: 3em; text-align: center; }
.slide-progress {
  position: fixed; top: 0; left: 0; height: 3px;
  background: var(--accent); z-index: 1001;
  transition: width 0.4s var(--transition-easing);
}

/* ========== REDUCED MOTION ========== */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
  .slide { transform: none !important; }
}

/* ========== RESPONSIVE ========== */
@media (max-width: 768px) {
  :root { --slide-padding: 2rem; }
  .slide-nav { gap: 0.5rem; padding: 0.4rem 0.8rem; }
}
@media (max-width: 480px) {
  :root { --slide-padding: 1.2rem; }
  .slide h1 { font-size: clamp(1.5rem, 7vw, 2rem); }
  .slide-nav { bottom: 1rem; }
  .nav-btn { width: 30px; height: 30px; font-size: 0.9rem; }
}
</style>
</head>
<body>

__PRESENTATION_OPEN__
__SLIDES__
</div>

<!-- Navigation -->
<nav class="slide-nav" aria-label="幻灯片导航">
  <button class="nav-btn" id="prevBtn" onclick="navigate(-1)" aria-label="上一页">‹</button>
  <div class="nav-dots" id="navDots"></div>
  <button class="nav-btn" id="nextBtn" onclick="navigate(1)" aria-label="下一页">›</button>
  <span class="slide-counter"><span id="slideCurrent">1</span> / <span id="slideTotal">__SLIDE_COUNT__</span></span>
</nav>

<div class="slide-progress" id="slideProgress" style="width:__PROGRESS_PERCENT__%;"></div>

<script>
(function() {
  'use strict';

  var slides = document.querySelectorAll('[data-slide]');
  var current = 0;
  var isAnimating = false;

  var dotsContainer = document.getElementById('navDots');
  for (var i = 0; i < slides.length; i++) {
    var dot = document.createElement('button');
    dot.className = 'nav-dot' + (i === 0 ? ' active' : '');
    dot.setAttribute('aria-label', '转到第 ' + (i + 1) + ' 页');
    dot.onclick = (function(idx) { return function() { goTo(idx); }; })(i);
    dotsContainer.appendChild(dot);
  }

  function goTo(index) {
    if (isAnimating) return;
    if (index < 0 || index >= slides.length) return;
    if (index === current) return;
    isAnimating = true;

    slides[current].classList.remove('active');
    slides[current].setAttribute('aria-hidden', 'true');
    slides[index].classList.add('active');
    slides[index].setAttribute('aria-hidden', 'false');
    current = index;

    document.getElementById('slideCurrent').textContent = current + 1;
    document.getElementById('slideProgress').style.width = ((current + 1) / slides.length * 100) + '%';

    var dots = dotsContainer.querySelectorAll('.nav-dot');
    for (var d = 0; d < dots.length; d++) {
      dots[d].classList.toggle('active', d === current);
    }
    document.getElementById('prevBtn').disabled = current === 0;
    document.getElementById('nextBtn').disabled = current === slides.length - 1;

    location.hash = 'slide-' + (current + 1);

    setTimeout(function() { isAnimating = false; }, 500);
  }

  function navigate(dir) {
    goTo(current + dir);
  }

  document.addEventListener('keydown', function(e) {
    var key = e.key;
    if (key === 'ArrowRight' || key === 'ArrowDown' || key === ' ') {
      e.preventDefault();
      navigate(1);
    } else if (key === 'ArrowLeft' || key === 'ArrowUp') {
      e.preventDefault();
      navigate(-1);
    } else if (key === 'Home') {
      e.preventDefault();
      goTo(0);
    } else if (key === 'End') {
      e.preventDefault();
      goTo(slides.length - 1);
    }
  });

  var presentation = document.getElementById('presentation');
  presentation.addEventListener('wheel', function(e) {
    if (isAnimating) return;
    var target = e.target;
    var scrollable = target.closest('[style*="overflow-y: auto"], [style*="overflow: auto"], .slide');
    if (scrollable && scrollable.scrollHeight > scrollable.clientHeight) {
      if (e.deltaY > 0 && scrollable.scrollTop + scrollable.clientHeight >= scrollable.scrollHeight - 2) {
        e.preventDefault();
        navigate(1);
      } else if (e.deltaY < 0 && scrollable.scrollTop <= 2) {
        e.preventDefault();
        navigate(-1);
      }
    } else {
      e.preventDefault();
      if (e.deltaY > 0) navigate(1);
      else navigate(-1);
    }
  }, { passive: false });

  presentation.addEventListener('click', function(e) {
    if (isAnimating) return;
    var tag = e.target.tagName;
    if (tag === 'A' || tag === 'BUTTON' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    if (e.target.closest('.slide-nav')) return;
    if (e.target.closest('a, button, input, select, textarea')) return;
    e.preventDefault();
    navigate(1);
  });

  (function() {
    var startX = 0, startY = 0;
    presentation.addEventListener('touchstart', function(e) {
      startX = e.changedTouches[0].screenX;
      startY = e.changedTouches[0].screenY;
    }, { passive: true });
    presentation.addEventListener('touchend', function(e) {
      var endX = e.changedTouches[0].screenX;
      var endY = e.changedTouches[0].screenY;
      var diffX = startX - endX;
      var diffY = startY - endY;
      if (Math.abs(diffX) > 50 && Math.abs(diffX) > Math.abs(diffY)) {
        if (diffX > 0) navigate(1);
        else navigate(-1);
      }
    }, { passive: true });
  })();

  if (location.hash) {
    var match = location.hash.match(/slide-(\d+)/);
    if (match) {
      var idx = parseInt(match[1], 10) - 1;
      if (idx >= 0 && idx < slides.length) {
        goTo(idx);
      }
    }
  }

  window.navigate = navigate;
  window.goTo = goTo;
})();
</script>
</body>
</html>
`
