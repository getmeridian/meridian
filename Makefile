.PHONY: help install sync test lint format check typecheck ci system-lab system-lab-fast \
       templates ai-docs build publish clean hooks

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

## —— CI (runs everything) ———————————————————————————————

ci: check templates ## Run full CI locally

system-lab: ## Run multi-node system lab (clean state, ~10min)
	bash tests/systemlab/scripts/setup-fixtures.sh
	docker compose -f tests/systemlab/compose.yml up --build --abort-on-container-exit --exit-code-from controller
	docker compose -f tests/systemlab/compose.yml down -v

system-lab-fast: ## Re-run system lab preserving cached images (~3-4min after first run)
	bash tests/systemlab/scripts/setup-fixtures.sh
	docker compose -f tests/systemlab/compose.yml up --build --abort-on-container-exit --exit-code-from controller
	docker compose -f tests/systemlab/compose.yml down

## —— Real-VM harness (LOCAL ONLY, costs real money) ————————————————

real-lab: ## Provision Hetzner VM + verify + destroy (TOPO=single by default)
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set. See tests/realvm/README.md"; exit 2; \
	fi
	@echo "  NOTE: this provisions a real VM and costs real money (~€0.01 for a full run)."
	PYTHONUNBUFFERED=1 uv run python -m tests.realvm.orchestrator up $${TOPO:-single}

real-lab-keep: ## Provision + verify, but DON'T auto-destroy (useful with TIER=interactive)
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set. See tests/realvm/README.md"; exit 2; \
	fi
	PYTHONUNBUFFERED=1 uv run python -m tests.realvm.orchestrator up $${TOPO:-single} --keep

real-lab-orphans: ## List harness-tagged VMs left behind in the Hetzner project
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set."; exit 2; \
	fi
	uv run python -m tests.realvm.orchestrator orphans

real-lab-down: ## Destroy ALL harness-tagged VMs in the project (orphan cleanup)
	@if [ -z "$$HCLOUD_TOKEN" ]; then \
		echo "  ✗ HCLOUD_TOKEN is not set."; exit 2; \
	fi
	uv run python -m tests.realvm.orchestrator down

## —— Build & Publish ————————————————————————————————————

ai-docs: ## Generate AI reference from human docs (strip frontmatter)
	@mkdir -p src/meridian/data
	@for f in website/src/content/docs/en/cli-reference.md \
	          website/src/content/docs/en/architecture.md \
	          website/src/content/docs/en/troubleshooting.md \
	          website/src/content/docs/en/deploy.md \
	          website/src/content/docs/en/relay.md \
	          website/src/content/docs/en/recovery.md; do \
		awk 'BEGIN{skip=0} /^---$$/{skip++;next} skip<2{next} {print}' "$$f"; \
		echo ""; \
	done > src/meridian/data/ai-reference.md

studio-assets: ## Build website assets bundled into `meridian studio`
	cd website && pnpm run contracts:check && pnpm run build
	cp VERSION website/dist/version
	find website/dist -name .DS_Store -delete

build: ai-docs studio-assets ## Build wheel and sdist
	uv build
	uv run python scripts/verify_package_assets.py dist/*.whl

publish: build ## Publish to PyPI (requires trusted publisher or token)
	uv publish

clean: ## Remove build artifacts
	rm -rf dist/ build/ .pytest_cache/ .mypy_cache/ .ruff_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
