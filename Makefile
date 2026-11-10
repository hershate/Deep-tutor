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
# 79#10b：探测落空时给出可执行提示而不是晦涩的 sh 报错
ifeq ($(GITBASH),)
$(warning 未在常见位置找到 Git Bash 的 sh.exe——请设 SHELL=/path/to/sh.exe 后重试（例如 Git 安装目录 bin/sh.exe）)
endif
endif

CARGO ?= cargo
NPM ?= npm

.PHONY: build lint test check dev-smoke hooks

build:
	# 许可收集挂在 tauri beforeBuildCommand（npm run build:bundle，49#1），
	# npm run tauri build 直连路径与 make/CI 路径同一口径
	# npm 吞掉第一个 --，脚本收到 build -- --locked，--locked 才能转发给 cargo
	cd desktop && $(NPM) run tauri build -- -- --locked

lint:
	cd desktop/src-tauri && $(CARGO) fmt --all -- --check
	node scripts/check-versions.mjs
	cd desktop && $(NPM) run build
	# 110#1：依赖审计入门禁——vite/esbuild dev 侧通告（含 Windows 命令
	# 执行族）原先无任何检出通道；runtime 与 dev 全量要求零漏洞
	cd desktop && $(NPM) audit --omit=dev && $(NPM) audit

test:
	# 串行执行：多个测试共享进程级全局（如 sync DEFER 标志），并行会互相污染
	cd desktop/src-tauri && $(CARGO) test --release --locked -- --test-threads=1

check: lint test dev-smoke

# 116#3：dev server 冒烟——111#2 实证过 vite 配置错误可让 check 全绿而
# dev 工作流 403，门禁盲区自此补上。专用高位端口（43198，strictPort）与
# 开发者常驻 dev server（1420）隔离；按 netstat 找到的监听 PID 精确停服
dev-smoke:
	bash scripts/dev-smoke.sh

hooks:
	git config core.hooksPath .githooks
