# The recipes below are POSIX shell (pipes, for-loops, `VAR=value cmd`). On
# Windows make would run them through cmd.exe, which understands neither, so
# name the shell that does. Git for Windows ships one; a machine without it
# falls back to /bin/sh, which is what Git Bash and MSYS2 provide.
#
# The path is found with a glob rather than written out: "Program Files"
# holds a space, and $(wildcard) cannot read a literal path with a space in
# it — it came back empty, the fallback /bin/sh does not exist on Windows,
# and make fell back to its own default: every recipe ran under cmd.exe.
#
# Not every make honours a makefile's SHELL: mingw32-make reads it from the
# environment only, so on that one the invocation is
# `make SHELL="C:/Program Files/Git/bin/sh.exe" build`.
ifeq ($(OS),Windows_NT)
# Git for Windows ships the POSIX shell the recipes run under. Its install
# path is not fixed: the drive changes with the machine, "Program Files"
# holds a space, and 8.3 short names may be off on the volume that holds it
# (a machine whose projects live on F: often has no short names there at
# all). So the shell is probed, not assumed.
#
# The shape of the probe matters as much as its result:
# - the pattern carries no space, so $(wildcard) can read it. A literal
#   path with a space is read as two patterns and finds nothing.
# - the result is never routed through $(foreach) or $(firstword): those
#   split on whitespace and would hand the shell half a path ("F:/Program").
# - the value keeps its space, and every use that reaches a shell quotes it.
#
# The last candidate asks git itself where it lives, so a Git installed
# outside a "Program Files" directory is still found. That path's space is
# escaped before $(wildcard) sees it, because an unescaped one splits the
# pattern in two.
empty :=
space := $(empty) $(empty)
# `git --exec-path` reads the same in cmd.exe and in a POSIX shell, so one
# command covers both. Its layout has changed over the years (mingw64,
# ucrt64), so the known tails are peeled off in turn.
GIT_EXEC_PATH := $(strip $(shell git --exec-path))
GIT_ROOT := $(patsubst %/ucrt64,%,$(patsubst %/mingw64,%,$(patsubst %/libexec/git-core,%,$(GIT_EXEC_PATH))))
GITBASH := $(or \
	$(wildcard C:/Progra~1/Git/bin/sh.exe), \
	$(wildcard F:/Progra~1/Git/bin/sh.exe), \
	$(wildcard $(subst $(space),\ ,$(GIT_ROOT))/bin/sh.exe))
# Quoted for $(shell ...): the argument text is split on whitespace before
# the value is expanded, so the quotes have to be part of the value.
GITBASH_QUOTED := $(if $(GITBASH),"$(GITBASH)",)
SHELL := $(if $(GITBASH),$(GITBASH),/bin/sh)
endif

# `$(shell ...)` is not the shell the recipes run under: mingw32-make runs it
# through cmd.exe whatever SHELL says. A command only a POSIX shell
# understands answers with something else there — `date -u` asks for a new
# date, a single-quoted sed script arrives with its quotes still on, and
# `cat` is not a command at all. The values below are therefore read in a
# way both understand, and the timestamp names the shell it needs.
#
# Nothing here redirects stderr: there is no spelling of the null device
# every one of these shells agrees on — `NUL` is an ordinary file to a POSIX
# shell, `/dev/null` is an ordinary file to cmd.exe — and a redirect that
# lands on a file leaves that file behind in the repository. A checkout make
# cannot read says so on stderr instead, which is worth saying.

# Exported rather than written inline in each recipe: an inline
# `CGO_ENABLED=0 go build` is a POSIX assignment, and cmd.exe would read the
# whole thing as the name of a program.
export CGO_ENABLED := 0

# The tag's own prefix is stripped by make rather than by a sed script: the
# pipe and the quotes are what cmd.exe mangles. A repository make cannot read
# leaves the value empty, and the fallback says so instead of half a
# command.
VERSION_RAW := $(strip $(shell git describe --tags --always))
VERSION := $(if $(VERSION_RAW),$(patsubst studio-%,%,$(VERSION_RAW)),dev)
GIT_COMMIT_RAW := $(strip $(shell git rev-parse --short=12 HEAD))
GIT_COMMIT := $(if $(GIT_COMMIT_RAW),$(GIT_COMMIT_RAW),unknown)

# The build's own time, in UTC and in a shape that fits in an -X value: it
# has to be one word, because a space in it splits the flag and the linker
# answers with its usage instead of a binary.
ifeq ($(OS),Windows_NT)
BUILD_TIME_UTC := $(strip $(if $(GITBASH),$(shell $(GITBASH_QUOTED) -c "date -u +%Y-%m-%dT%H:%M:%SZ"),))
ifeq ($(BUILD_TIME_UTC),)
BUILD_TIME_UTC := $(strip $(shell powershell -NoProfile -Command "[DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss') + 'Z'"))
endif
else
BUILD_TIME_UTC := $(strip $(shell date -u +%Y-%m-%dT%H:%M:%SZ))
endif

LDFLAGS := -s -w \
	-X main.version=$(VERSION) \
	-X main.gitCommit=$(GIT_COMMIT) \
	-X main.buildTimeUTC=$(BUILD_TIME_UTC)
GOEXE := $(shell go env GOEXE)
# One pin for the Makefile and the CI lint job; see .github/workflows/ci.yml.
# make reads the pin itself where it can: `$(file <name)` needs make 4.0 and
# macOS still ships 3.81, where `cat` is the way in.
ifeq ($(findstring 3.81,$(MAKE_VERSION)),)
GOLANGCI_VERSION := $(strip $(file <.golangci-version))
else
GOLANGCI_VERSION := $(strip $(shell cat .golangci-version))
endif

.PHONY: build vet fmt lint lint-go lint-install lint-cross lint-update coverage-gate coverage-gate-update pricecheck check test studio-test sdk-test sdk-test-race hooks cross clean studio

build:
	go build -ldflags "$(LDFLAGS)" -o bin/deep-tutor$(GOEXE) ./cmd/deep-tutor
	go build -ldflags "$(LDFLAGS)" -o bin/reasonix-plugin-example$(GOEXE) ./cmd/reasonix-plugin-example

vet:
	go vet ./...

# Build Studio and launch it. The Electron shell is the one this runs: it is a
# real GUI app on every platform without being bundled first, so there is one
# launch path rather than a macOS one and an everywhere-else one.
studio:
	bash desktop/electron/run-studio.sh


fmt:
	gofmt -w .

# Both gates CI runs, at the version CI pins. Skipping golangci-lint locally
# trades a second here for a ten-minute CI round trip: `modernize` findings in
# particular never surface in `go vet`.
lint: lint-go
	go run ./tools/repolint

lint-go:
	@command -v golangci-lint >/dev/null || { echo "golangci-lint not installed; run: make lint-install"; exit 1; }
	@have=$$(golangci-lint version --short 2>/dev/null); want=$$(echo "$(GOLANGCI_VERSION)" | sed 's/^v//'); \
		[ "$$have" = "$$want" ] || echo "warning: local golangci-lint $$have, CI pins $$want (make lint-install)"
	golangci-lint run --timeout=5m ./...

# CGO_ENABLED=0 keeps the install working where a stray clang on PATH shadows
# the toolchain and breaks runtime/cgo. The export above already sets it, so
# the recipe itself stays readable by a shell that knows no assignments.
lint-install:
	go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@$(GOLANGCI_VERSION)

# repolint over what the working tree changed. It exists as a target rather than
# a command to paste because the host recognizes `make check` as a verification
# it can read the result of, while an arbitrary `go run ./tools/...` is an
# unknown program it must assume writes — so only this form can be cited as
# evidence that the standards gate passed.
check:
	go run ./tools/repolint -changed

lint-update:
	go run ./tools/repolint -update

# The paths the project declared sensitive, held to the coverage they already
# have. It runs their tests, so it is its own target rather than part of the
# fast `make lint`. -update records the current numbers; lowering one needs
# -allow-drop, the way widening a repolint budget does.
coverage-gate:
	go run ./tools/covergate

coverage-gate-update:
	go run ./tools/covergate -update

# The vendors publish their rates on pages this reads and compares the table
# against. It reaches the network, so it stays out of lint and out of CI: run it
# before touching a rate and when a vendor announces a change. A page it cannot
# read exits non-zero — unread never counts as agreement.
pricecheck:
	go run ./tools/pricecheck

# Linting one GOOS leaves every //go:build windows and darwin file unchecked.
lint-cross:
	@for t in "linux ." "darwin ." "windows ." "linux desktop" "windows desktop"; do \
		set -- $$t; \
		echo "== golangci-lint GOOS=$$1 ($$2)"; \
		(cd $$2 && GOOS=$$1 golangci-lint run --timeout=5m ./...) || exit 1; \
	done

# Every module, because a nested one is where a red test hides longest: the
# shell's route allowlist sat broken through a release, and `go test ./...` from
# the root cannot see it.
#
# The root module runs under tools/canary: the suite is pointed at a disposable
# home and the run fails when the suite wrote into it. A test that writes into
# the user's own home is silent, and the damage lands on the user's next start
# rather than on this run.
test: studio-test sdk-test
	go run ./tools/canary run -- ./...

# The shell is a nested module, so the root `test` target never reaches it.
studio-test:
	cd desktop && go test ./...

sdk-test:
	cd sdk/go && go test ./...

sdk-test-race:
	cd sdk/go && go test -race ./...

hooks:
	@git config core.hooksPath .githooks
	@echo "installed: core.hooksPath -> .githooks (pre-push runs go vet)"

cross:
	@mkdir -p dist
	@for p in darwin/amd64 darwin/arm64 linux/amd64 linux/arm64 windows/amd64 windows/arm64; do \
		os=$${p%/*}; arch=$${p#*/}; ext=; [ $$os = windows ] && ext=.exe; \
		echo "build $$os/$$arch"; \
		CGO_ENABLED=0 GOOS=$$os GOARCH=$$arch go build -ldflags "$(LDFLAGS)" -o dist/deep-tutor-$$os-$$arch$$ext ./cmd/deep-tutor; \
	done

clean:
	rm -rf bin dist
