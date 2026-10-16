package tutor

// apkg.go builds an Anki .apkg card package from a plan's quizzes —
// Deep-tutor's build_anki as a native writer: a ZIP with collection.anki2
// (SQLite schema-11: col/notes/cards) and an empty media manifest.

import (
	"archive/zip"
	"bytes"
	"crypto/sha1"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"html"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"

	_ "modernc.org/sqlite" // registers the "sqlite" driver for collection.anki2
)

// ErrNoQuizzes marks a plan whose quizzes hold nothing exportable.
var ErrNoQuizzes = errors.New("tutor: the plan has no quizzes to export")

// ErrNoWrongQuizzes marks a wrong-only export with nothing answered wrong.
var ErrNoWrongQuizzes = errors.New("tutor: no wrong quizzes to export")

const (
	apkgModelID      int64 = 2026101401
	apkgDeckIDBase   int64 = 2026101400
	apkgWrongDeckGap int64 = 100_000_000
	apkgModelName          = "Reasonix Tutor 知识点卡片"
	apkgModelCSS           = ".card{font-family:sans-serif;font-size:18px;line-height:1.6}" +
		".front b{color:#2563eb}.back{color:#334155}.opts{margin-top:8px}" +
		".exp{margin-top:8px;color:#64748b;font-size:15px}"
	apkgCardQfmt = "<div class='front'>{{Front}}</div>"
	apkgCardAfmt = "{{FrontSide}}<hr id='answer'><div class='back'>{{Back}}</div>"
)

// LatestAttempts keeps each quiz's most recent attempt.
func LatestAttempts(attempts []QuizAttempt) map[string]QuizAttempt {
	latest := make(map[string]QuizAttempt, len(attempts))
	for _, a := range attempts {
		if prev, ok := latest[a.QuizID]; !ok || !a.At.Before(prev.At) {
			latest[a.QuizID] = a
		}
	}
	return latest
}

// LatestWrongQuizzes filters to quizzes whose latest attempt graded false —
// the wrong-book's criterion, shared with the wrong-only apkg export.
func LatestWrongQuizzes(quizzes []Quiz, attempts []QuizAttempt) []Quiz {
	latest := LatestAttempts(attempts)
	kept := make([]Quiz, 0, len(quizzes))
	for _, q := range quizzes {
		if a, ok := latest[q.ID]; ok && a.Correct != nil && !*a.Correct {
			kept = append(kept, q)
		}
	}
	return kept
}

func apkgEsc(s string) string { return html.EscapeString(s) }

// apkgCardFront is the question side: the question, then the options list
// for choice quizzes. Anki renders fields as HTML, so everything is escaped.
func apkgCardFront(p QuizPayload) string {
	var b strings.Builder
	b.WriteString("<b>Q</b>：")
	b.WriteString(apkgEsc(strings.TrimSpace(p.Question)))
	if len(p.Options) > 0 {
		b.WriteString("<div class='opts'>")
		for _, o := range p.Options {
			b.WriteString("<div>")
			b.WriteString(apkgEsc(o))
			b.WriteString("</div>")
		}
		b.WriteString("</div>")
	}
	return b.String()
}

// apkgCardBack is the answer side: the answer plus the explanation when the
// generator produced one.
func apkgCardBack(p QuizPayload) string {
	var b strings.Builder
	b.WriteString("<b>A</b>：")
	b.WriteString(apkgEsc(strings.TrimSpace(p.Answer)))
	if e := strings.TrimSpace(p.Explanation); e != "" {
		b.WriteString("<div class='exp'>")
		b.WriteString(apkgEsc(e))
		b.WriteString("</div>")
	}
	return b.String()
}

// apkgGUID derives a note guid from the card content — genanki's default.
// Stable across exports, so re-importing an updated deck refreshes the
// matching notes instead of duplicating them.
func apkgGUID(question, answer string) string {
	sum := sha256.Sum256([]byte(question + "\x1f" + answer))
	return base64.StdEncoding.EncodeToString(sum[:8])
}

var apkgTagRe = regexp.MustCompile(`<[^>]*>`)

// apkgSortText strips markup for the sort field, matching Anki's stripHTML
// closely enough for browser display and dupe detection.
func apkgSortText(s string) string {
	return html.UnescapeString(apkgTagRe.ReplaceAllString(s, ""))
}

// apkgFieldChecksum is Anki's field checksum: the first 8 hex digits of the
// SHA-1 over the stripped sort field.
func apkgFieldChecksum(s string) int64 {
	sum := sha1.Sum([]byte(s))
	n, err := strconv.ParseInt(hex.EncodeToString(sum[:])[:8], 16, 64)
	if err != nil {
		return 0
	}
	return n
}

// apkgDeckID derives a stable deck id from the plan id: repeated exports of
// one plan land in the same deck, and the wrong-book variant sits a fixed
// offset away so both decks coexist without overwriting each other.
func apkgDeckID(planID string, wrongOnly bool) int64 {
	h := fnv.New32a()
	_, _ = h.Write([]byte(planID))
	id := apkgDeckIDBase + int64(h.Sum32()%100000)
	if wrongOnly {
		id += apkgWrongDeckGap
	}
	return id
}

func apkgDeckName(plan *Plan, wrongOnly bool) string {
	title := plan.Title
	if title == "" {
		title = plan.Topic
	}
	if title == "" {
		title = "学习计划"
	}
	name := "Reasonix Tutor::" + title
	if wrongOnly {
		name += "·错题"
	}
	return name
}

type apkgCard struct {
	front, back, guid string
}

// apkgBuildCards filters and dedupes: a card needs a non-empty question and
// answer, and one note per distinct question/answer pair (a shared guid
// inside one collection would collapse on import anyway).
func apkgBuildCards(quizzes []Quiz) []apkgCard {
	cards := make([]apkgCard, 0, len(quizzes))
	seen := map[string]bool{}
	for _, q := range quizzes {
		question := strings.TrimSpace(q.Payload.Question)
		answer := strings.TrimSpace(q.Payload.Answer)
		if question == "" || answer == "" {
			continue
		}
		guid := apkgGUID(question, answer)
		if seen[guid] {
			continue
		}
		seen[guid] = true
		cards = append(cards, apkgCard{front: apkgCardFront(q.Payload), back: apkgCardBack(q.Payload), guid: guid})
	}
	return cards
}

// The col row carries the model/deck registries as JSON columns; the shapes
// below mirror what Anki's schema-11 importer expects to find in them.

type apkgModelJSON struct {
	CSS  string `json:"css"`
	DID  int64  `json:"did"`
	Flds []struct {
		Font   string `json:"font"`
		Media  []any  `json:"media"`
		Name   string `json:"name"`
		Ord    int    `json:"ord"`
		Rtl    bool   `json:"rtl"`
		Size   int    `json:"size"`
		Sticky bool   `json:"sticky"`
	} `json:"flds"`
	ID        int64  `json:"id"`
	LatexPost string `json:"latexPost"`
	LatexPre  string `json:"latexPre"`
	Mod       int64  `json:"mod"`
	Name      string `json:"name"`
	Sortf     int    `json:"sortf"`
	Tags      []any  `json:"tags"`
	Tmpls     []struct {
		Afmt  string `json:"afmt"`
		Bafmt string `json:"bafmt"`
		Bqfmt string `json:"bqfmt"`
		Did   *int64 `json:"did"`
		Name  string `json:"name"`
		Ord   int    `json:"ord"`
		Qfmt  string `json:"qfmt"`
	} `json:"tmpls"`
	Type int   `json:"type"`
	Usn  int64 `json:"usn"`
	Vers []any `json:"vers"`
}

type apkgDeckJSON struct {
	BrowserCollapsed bool   `json:"browserCollapsed"`
	Collapsed        bool   `json:"collapsed"`
	Conf             int64  `json:"conf"`
	Desc             string `json:"desc"`
	Dyn              int    `json:"dyn"`
	ExtendNew        int    `json:"extendNew"`
	ExtendRev        int    `json:"extendRev"`
	ID               int64  `json:"id"`
	LrnToday         [2]int `json:"lrnToday"`
	Mod              int64  `json:"mod"`
	Name             string `json:"name"`
	NewToday         [2]int `json:"newToday"`
	RevToday         [2]int `json:"revToday"`
	TimeToday        [2]int `json:"timeToday"`
	Usn              int64  `json:"usn"`
}

func apkgModelRegistry(deckID, nowMS int64) (string, error) {
	var m apkgModelJSON
	m.CSS, m.DID, m.ID = apkgModelCSS, deckID, apkgModelID
	m.Flds = []struct {
		Font   string `json:"font"`
		Media  []any  `json:"media"`
		Name   string `json:"name"`
		Ord    int    `json:"ord"`
		Rtl    bool   `json:"rtl"`
		Size   int    `json:"size"`
		Sticky bool   `json:"sticky"`
	}{
		{Font: "Liberation Sans", Media: []any{}, Name: "Front", Ord: 0, Size: 20},
		{Font: "Liberation Sans", Media: []any{}, Name: "Back", Ord: 1, Size: 20},
	}
	m.LatexPost = "\\end{document}"
	m.LatexPre = "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n" +
		"\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n" +
		"\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n"
	m.Mod, m.Name, m.Sortf = nowMS, apkgModelName, 0
	m.Tags, m.Vers = []any{}, []any{}
	m.Tmpls = []struct {
		Afmt  string `json:"afmt"`
		Bafmt string `json:"bafmt"`
		Bqfmt string `json:"bqfmt"`
		Did   *int64 `json:"did"`
		Name  string `json:"name"`
		Ord   int    `json:"ord"`
		Qfmt  string `json:"qfmt"`
	}{
		{Afmt: apkgCardAfmt, Bqfmt: "", Bafmt: "", Did: nil, Name: "卡片", Ord: 0, Qfmt: apkgCardQfmt},
	}
	m.Type, m.Usn = 0, -1
	blob, err := json.Marshal(map[string]apkgModelJSON{strconv.FormatInt(m.ID, 10): m})
	if err != nil {
		return "", err
	}
	return string(blob), nil
}

func apkgDeckRegistry(deckID int64, deckName string, nowS int64) (string, error) {
	newDeck := func(id int64, name string, usn int64) apkgDeckJSON {
		return apkgDeckJSON{
			Conf: 1, Dyn: 0, ExtendNew: 10, ExtendRev: 50,
			ID: id, Mod: nowS, Name: name, Usn: usn,
		}
	}
	blob, err := json.Marshal(map[string]apkgDeckJSON{
		"1":                           newDeck(1, "Default", 0),
		strconv.FormatInt(deckID, 10): newDeck(deckID, deckName, -1),
	})
	if err != nil {
		return "", err
	}
	return string(blob), nil
}

func apkgColConf(modelID int64, nextPos int) (string, error) {
	blob, err := json.Marshal(map[string]any{
		"activeDecks": []int64{1}, "addToCur": true, "collapseTime": 1200,
		"curDeck": 1, "curModel": strconv.FormatInt(modelID, 10),
		"dueCounts": true, "estTimes": true, "newBury": true, "newSpread": 0,
		"nextPos": nextPos, "sortBackwards": false, "sortType": "noteFld", "timeLim": 0,
	})
	if err != nil {
		return "", err
	}
	return string(blob), nil
}

func apkgDeckConf() (string, error) {
	blob, err := json.Marshal(map[string]any{
		"1": map[string]any{
			"autoplay": true, "id": 1, "maxTaken": 60, "name": "Default",
			"replayq": true, "timer": 0, "usn": 0,
			"lapse": map[string]any{
				"delays": []int{10}, "leechAction": 0, "leechFails": 8,
				"minInt": 1, "mult": 0,
			},
			"new": map[string]any{
				"bury": true, "delays": []int{1, 10}, "initialFactor": 2500,
				"ints": []int{1, 4, 7}, "order": 1, "perDay": 20, "separate": true,
			},
			"rev": map[string]any{
				"bury": true, "ease4": 1.3, "fuzz": 0.05, "ivlFct": 1,
				"maxIvl": 36500, "minSpace": 1, "perDay": 100,
			},
		},
	})
	if err != nil {
		return "", err
	}
	return string(blob), nil
}

var apkgSchema = []string{
	`CREATE TABLE col (
		id integer primary key,
		crt integer not null,
		mod integer not null,
		scm integer not null,
		ver integer not null,
		dty integer not null,
		usn integer not null,
		ls integer not null,
		conf text not null,
		models text not null,
		decks text not null,
		dconf text not null,
		tags text not null
	)`,
	`CREATE TABLE notes (
		id integer primary key,
		guid text not null,
		mid integer not null,
		mod integer not null,
		usn integer not null,
		tags text not null,
		flds text not null,
		sfld integer not null,
		csum integer not null,
		flags integer not null,
		data text not null
	)`,
	`CREATE TABLE cards (
		id integer primary key,
		nid integer not null,
		did integer not null,
		ord integer not null,
		mod integer not null,
		usn integer not null,
		type integer not null,
		queue integer not null,
		due integer not null,
		ivl integer not null,
		factor integer not null,
		reps integer not null,
		lapses integer not null,
		left integer not null,
		odue integer not null,
		odid integer not null,
		flags integer not null,
		data text not null
	)`,
	`CREATE TABLE revlog (
		id integer primary key,
		cid integer not null,
		usn integer not null,
		ease integer not null,
		ivl integer not null,
		lastIvl integer not null,
		factor integer not null,
		time integer not null,
		type integer not null
	)`,
	`CREATE TABLE graves (
		usn integer not null,
		oid integer not null,
		type integer not null
	)`,
	`CREATE INDEX ix_notes_usn on notes (usn)`,
	`CREATE INDEX ix_cards_usn on cards (usn)`,
	`CREATE INDEX ix_revlog_usn on revlog (usn)`,
	`CREATE INDEX ix_cards_nid on cards (nid)`,
	`CREATE INDEX ix_cards_sched on cards (did, queue, due)`,
	`CREATE INDEX ix_revlog_cid on revlog (cid)`,
	`CREATE INDEX ix_notes_csum on notes (csum)`,
}

// apkgWriteCollection builds the schema-11 collection into the file at path.
func apkgWriteCollection(path string, cards []apkgCard, deckID int64, deckName string, now time.Time) error {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return err
	}
	defer db.Close()
	for _, stmt := range apkgSchema {
		if _, err := db.Exec(stmt); err != nil {
			return fmt.Errorf("tutor: apkg schema: %w", err)
		}
	}
	nowMS, nowS := now.UnixMilli(), now.Unix()
	models, err := apkgModelRegistry(deckID, nowMS)
	if err != nil {
		return err
	}
	decks, err := apkgDeckRegistry(deckID, deckName, nowS)
	if err != nil {
		return err
	}
	conf, err := apkgColConf(apkgModelID, len(cards)+1)
	if err != nil {
		return err
	}
	dconf, err := apkgDeckConf()
	if err != nil {
		return err
	}
	// crt anchors the collection's day-rollover clock at local midnight.
	y, m, d := now.Date()
	crt := time.Date(y, m, d, 0, 0, 0, 0, now.Location()).Unix()
	if _, err := db.Exec(
		`INSERT INTO col (id,crt,mod,scm,ver,dty,usn,ls,conf,models,decks,dconf,tags)
		 VALUES (1,?,?,?,11,0,0,0,?,?,?,?,'{}')`,
		crt, nowMS, nowMS, conf, models, decks, dconf,
	); err != nil {
		return fmt.Errorf("tutor: apkg col: %w", err)
	}
	for i, c := range cards {
		sortText := apkgSortText(c.front)
		if _, err := db.Exec(
			`INSERT INTO notes (id,guid,mid,mod,usn,tags,flds,sfld,csum,flags,data)
			 VALUES (?,?,?,?,-1,'',?,?,?,0,'')`,
			nowMS+int64(i), c.guid, apkgModelID, nowMS, c.front+"\x1f"+c.back, sortText, apkgFieldChecksum(sortText),
		); err != nil {
			return fmt.Errorf("tutor: apkg note: %w", err)
		}
		if _, err := db.Exec(
			`INSERT INTO cards (id,nid,did,ord,mod,usn,type,queue,due,ivl,factor,reps,lapses,left,odue,odid,flags,data)
			 VALUES (?,?,?,0,?,-1,0,0,?,0,0,0,0,0,0,0,0,'')`,
			nowMS+int64(len(cards))+int64(i), nowMS+int64(i), deckID, nowS, i+1,
		); err != nil {
			return fmt.Errorf("tutor: apkg card: %w", err)
		}
	}
	return db.Close()
}

// BuildApkg renders quizzes as .apkg bytes and reports the card count.
// wrongOnly selects the wrong-book variant: only quizzes whose latest
// attempt graded false, exported under a sibling deck. Nothing exportable is
// ErrNoQuizzes (or ErrNoWrongQuizzes for the empty wrong-book).
func BuildApkg(plan *Plan, quizzes []Quiz, attempts []QuizAttempt, wrongOnly bool, now time.Time) ([]byte, int, error) {
	if wrongOnly {
		quizzes = LatestWrongQuizzes(quizzes, attempts)
		if len(quizzes) == 0 {
			return nil, 0, ErrNoWrongQuizzes
		}
	}
	cards := apkgBuildCards(quizzes)
	if len(cards) == 0 {
		return nil, 0, ErrNoQuizzes
	}
	tmp, err := os.CreateTemp("", "reasonix-apkg-*.anki2")
	if err != nil {
		return nil, 0, err
	}
	path := tmp.Name()
	tmp.Close()
	defer os.Remove(path)
	if err := apkgWriteCollection(path, cards, apkgDeckID(plan.ID, wrongOnly), apkgDeckName(plan, wrongOnly), now); err != nil {
		return nil, 0, err
	}
	dbBytes, err := os.ReadFile(path)
	if err != nil {
		return nil, 0, err
	}
	var zipBuf bytes.Buffer
	zw := zip.NewWriter(&zipBuf)
	entry, err := zw.Create("collection.anki2")
	if err != nil {
		return nil, 0, err
	}
	if _, err := entry.Write(dbBytes); err != nil {
		return nil, 0, err
	}
	mediaEntry, err := zw.Create("media")
	if err != nil {
		return nil, 0, err
	}
	if _, err := mediaEntry.Write([]byte("{}")); err != nil {
		return nil, 0, err
	}
	if err := zw.Close(); err != nil {
		return nil, 0, err
	}
	return zipBuf.Bytes(), len(cards), nil
}
