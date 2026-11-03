ifeq ($(OS),Windows_NT)
empty :=
space := $(empty) $(empty)
GIT_EXEC_PATH := $(strip $(shell git --exec-path))
GIT_ROOT := $(patsubst %/ucrt64,%,$(patsubst %/mingw64,%,$(patsubst %/libexec/git-core,%,$(GIT_EXEC_PATH))))
GITBASH := $(or \
	$(wildcard C:/Progra~1/Git/bin/sh.exe), \
	$(wildcard F:/Progra~1/Git/bin/sh.exe), \
	$(wildcard $(subst $(space),\ ,$(GIT_ROOT))/bin/sh.exe))
SHELL := $(if $(GITBASH),$(GITBASH),/bin/sh)
endif

CARGO ?= cargo
NPM ?= npm

.PHONY: build lint test check hooks

build:
	cd desktop && $(NPM) run tauri build

lint:
	cd desktop/src-tauri && $(CARGO) fmt --all -- --check
	cd desktop && $(NPM) run build

test:
	cd desktop/src-tauri && $(CARGO) test --release --locked -- --test-threads=1

check: lint test

hooks:
	git config core.hooksPath .githooks
