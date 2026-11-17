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

# 131#3：build 前置 lint（fmt+版本+前端构建+audit）——原先坏树可跳过
# check 直接产出安装包；测试不前置：全量 release 测试耗时过长，
# 发布前以 make check（lint test dev-smoke）兜底
build: lint
	# 许可收集挂在 tauri beforeBuildCommand（npm run build:bundle，49#1），
	# npm run tauri build 直连路径与 make/CI 路径同一口径
	# npm 吞掉第一个 --，脚本收到 build -- --locked，--locked 才能转发给 cargo
	cd desktop && $(NPM) run tauri build -- -- --locked

lint:
	cd desktop/src-tauri && $(CARGO) fmt --all -- --check
	node scripts/check-versions.mjs
	# 架构文档规模指标核对（P-152/P-153）：注册命令数、前端调用数、§7 分组
	# 与「按源码核对」的前端文件/页面/组件/客户端文件数、业务域数、
	# Rust 测试数此前靠人工誊写，数字漂移无检出通道
	node scripts/check-doc-metrics.mjs
	cd desktop && $(NPM) run build
	# 110#1：依赖审计入门禁——vite/esbuild dev 侧通告（含 Windows 命令
	# 执行族）原先无任何检出通道；runtime 与 dev 全量要求零漏洞
	# runtime 口径保持严格零漏洞、不带任何例外
	cd desktop && $(NPM) audit --omit=dev
	# dev 全量口径改由例外感知脚本判定（110#10）：只放行白名单中的确切
	# 通告 ID——当前仅 GHSA-fc5q-39xc-vjpf / CVE-2026-112366（picomatch，
	# 上游无修复版；理由、可达性与退出条件见 scripts/audit-exceptions.mjs
	# 与 AGENTS.md）；白名单外的任何通告一律以非零退出码失败。
	# 退出条件：picomatch 发布含上游修复的 ≥4.0.8 且 vite 依赖链取用后，
	# 删除脚本内白名单条目并把本行恢复为 `cd desktop && $(NPM) audit`
	node scripts/audit-exceptions.mjs

test:
	# 前端回归测试：Markdown 预处理（公式定界符改写）与主题缓存分区此前
	# 只靠临时探针验证，改动后无兜底——用 Node 自带 test runner 补上，
	# 不新增依赖
	node desktop/scripts/frontend-test.mjs
	# 构建脚本回归测试：旧版本安装包清理（P-154）此前无自动化通道
	node desktop/scripts/build-test.mjs
	# 串行执行：多个测试共享进程级全局（如 sync DEFER 标志），并行会互相污染
	cd desktop/src-tauri && $(CARGO) test --release --locked -- --test-threads=1

check: lint test dev-smoke

# 116#3：dev server 冒烟——111#2 实证过 vite 配置错误可让 check 全绿而
# dev 工作流 403，门禁盲区自此补上。专用高位端口（43198，strictPort）与
# 开发者常驻 dev server（1420）隔离；启动前探测端口空闲，cleanup 只终止
# 自身启动的进程树（131#1/#2/#9，详见 scripts/dev-smoke.sh）
dev-smoke:
	bash scripts/dev-smoke.sh

hooks:
	git config core.hooksPath .githooks
