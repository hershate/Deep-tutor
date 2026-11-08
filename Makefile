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
	# 许可收集挂在 tauri beforeBuildCommand（npm run build:bundle，49#1），
	# npm run tauri build 直连路径与 make/CI 路径同一口径
	# npm 吞掉第一个 --，脚本收到 build -- --locked，--locked 才能转发给 cargo
	cd desktop && $(NPM) run tauri build -- -- --locked

lint:
	cd desktop/src-tauri && $(CARGO) fmt --all -- --check
	node scripts/check-versions.mjs
	cd desktop && $(NPM) run build

test:
	# 串行执行：多个测试共享进程级全局（如 sync DEFER 标志），并行会互相污染
	cd desktop/src-tauri && $(CARGO) test --release --locked -- --test-threads=1

check: lint test

hooks:
	git config core.hooksPath .githooks
