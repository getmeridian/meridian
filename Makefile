.PHONY: help install sync test lint format check typecheck validate dead-code ci feedback-check system-lab-preflight system-lab-agent system-lab system-lab-fast \
       templates dead-code ai-docs build publish clean hooks agent

## —— Setup ——————————————————————————————————————————————

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ai-docs ## Install package in dev mode with all dependencies
	uv sync --extra dev --reinstall-package meridian-vpn

sync: install ## Alias for install

hooks: ## Install git pre-push hook
	git config core.hooksPath .githooks
	@echo "  ✓ Git hooks installed (.githooks/pre-push)"

## —— Quality ————————————————————————————————————————————

test: ## Run Python tests
	uv run pytest tests/ -v --tb=short

lint: ## Run ruff linter
	uv run ruff check src/ tests/

format: ## Auto-format code
	uv run ruff format src/ tests/

format-check: ## Check formatting without changes
	uv run ruff format --check src/ tests/

typecheck: ## Run mypy type checker
	uv run mypy src/meridian/

check: lint format-check test ## Run all Python checks (lint + format + test)

templates: ## Validate Jinja2 template rendering
	uv run python tests/render_templates.py

validate: ## Check CLI docs, app list sync, and generated contracts
	uv run python tests/validate_cli_docs.py
	uv run python tests/validate_apps_sync.py
	uv run python scripts/export_contracts.py --check
	uv run python scripts/validate_api_docs.py

dead-code: ## Report dead / test-only code and unused params (warnings, never fails)
	@echo "  —— Dead-code report (warnings only) ——"
	-@uv run python scripts/find_dead_code.py

## —— CI (runs everything) ———————————————————————————————

ci: check typecheck templates validate dead-code ## Run full CI locally

feedback-check: ## Test, type-check and build the standalone Telegram feedback bot
	cd feedback-bot && npm ci --ignore-scripts && npm run check

system-lab-preflight: ## Check Compose, daemon registry access, and container DNS
	bash tests/systemlab/scripts/preflight.sh

# The lab's servers run the Docker daemon's architecture; build just that agent target.
system-lab-agent:
	@arch=$$(docker info --format '{{.Architecture}}'); \
		case "$$arch" in x86_64|amd64) target=x86_64-unknown-linux-musl ;; aarch64|arm64) target=aarch64-unknown-linux-musl ;; \
		*) echo "System lab has no agent target for $$arch" >&2; exit 2 ;; esac; \
		$(MAKE) agent AGENT_TARGETS=$$target

system-lab: system-lab-preflight system-lab-agent ## Run multi-node system lab (clean state, ~10min)
	bash tests/systemlab/scripts/setup-fixtures.sh
	@set -eu; \
		cleanup() { docker compose -f tests/systemlab/compose.yml down -v; }; \
		cleanup_on_exit() { \
			status=$$?; trap - EXIT; cleanup_status=0; \
			cleanup || cleanup_status=$$?; \
			if [ "$$status" -ne 0 ]; then exit "$$status"; fi; \
			exit "$$cleanup_status"; \
		}; \
		trap cleanup_on_exit EXIT; \
		cleanup; \
		docker compose -f tests/systemlab/compose.yml up --build --abort-on-container-exit --exit-code-from controller

system-lab-fast: system-lab-preflight system-lab-agent ## Re-run system lab preserving cached images (~3-4min after first run)
	bash tests/systemlab/scripts/setup-fixtures.sh
	@set -eu; \
		cleanup() { docker compose -f tests/systemlab/compose.yml down; }; \
		cleanup_on_exit() { \
			status=$$?; trap - EXIT; cleanup_status=0; \
			cleanup || cleanup_status=$$?; \
			if [ "$$status" -ne 0 ]; then exit "$$status"; fi; \
			exit "$$cleanup_status"; \
		}; \
		trap cleanup_on_exit EXIT; \
		cleanup; \
		docker compose -f tests/systemlab/compose.yml up --build --abort-on-container-exit --exit-code-from controller

## —— Real-VM harness (LOCAL ONLY, costs real money) ————————————————

real-lab: ## Provision Hetzner VM + verify + destroy (TOPO=single by default)
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set. See tests/realvm/README.md"; exit 2; \
	fi
	@echo "  NOTE: this provisions a real VM and costs real money (~€0.01 for a full run)."
	PYTHONUNBUFFERED=1 uv run --group realvm python -m tests.realvm.orchestrator up $${TOPO:-single}

real-lab-keep: ## Provision + verify, but DON'T auto-destroy (useful with TIER=interactive)
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set. See tests/realvm/README.md"; exit 2; \
	fi
	PYTHONUNBUFFERED=1 uv run --group realvm python -m tests.realvm.orchestrator up $${TOPO:-single} --keep

real-lab-up: ## Create a lab that stays up (NODES="ctl:hel1 exit:fsn1:debian-13 ..."); see tests/realvm/README.md
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set. See tests/realvm/README.md"; exit 2; \
	fi
	PYTHONUNBUFFERED=1 uv run --group realvm python -m tests.realvm.lab up $${NODES:-ctl:hel1}

real-lab-orphans: ## List harness-tagged VMs left behind in the Hetzner project
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set."; exit 2; \
	fi
	uv run --group realvm python -m tests.realvm.orchestrator orphans

real-lab-down: ## Destroy ALL harness-tagged VMs in the project (orphan cleanup)
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set."; exit 2; \
	fi
	uv run --group realvm python -m tests.realvm.orchestrator down

## —— Build & Publish ————————————————————————————————————

ai-docs: ## Bundle the docs `doctor --ai` pastes into its prompt (needs Node)
	@if command -v node >/dev/null 2>&1; then \
		node website/scripts/ai-docs.mjs reference src/meridian/data/ai-reference.md; \
	else \
		echo "  - Node.js not found; skipping the AI reference (doctor --ai works without it)"; \
	fi

# Pinned toolchain image (multi-arch index digest), run on the host's own architecture;
# agent/scripts/build-musl.sh cross-compiles the other target without emulation.
AGENT_IMAGE := rust:1.99.1-alpine3.22@sha256:731f64e16c12af70970d9a3655eecb1eecab4898352f09688a8e5ca2ab8b77c5
AGENT_TARGETS ?= x86_64-unknown-linux-musl aarch64-unknown-linux-musl

agent: ## Build static netagent binaries (musl, in Docker) into agent/dist/
	@mkdir -p agent/dist
	docker run --rm -v "$(CURDIR)/agent:/src:ro" -v "$(CURDIR)/agent/dist:/out" \
		-v meridian-agent-registry:/usr/local/cargo/registry -v meridian-agent-target:/target \
		-e CARGO_TARGET_DIR=/target -w /src $(AGENT_IMAGE) sh /src/scripts/build-musl.sh $(AGENT_TARGETS)
	@ls -l $(foreach t,$(AGENT_TARGETS),agent/dist/netagent-$(t))

build: ai-docs ## Build wheel and sdist
	uv build
	uv run python scripts/verify_package_assets.py dist/*.whl

publish: build ## Publish to PyPI (requires trusted publisher or token)
	uv publish

clean: ## Remove build artifacts
	rm -rf dist/ build/ .pytest_cache/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
