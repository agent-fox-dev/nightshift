MODULE  := github.com/agent-fox-dev/nightshift
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
LDFLAGS := -X $(MODULE)/internal/buildinfo.Version=$(VERSION)

.PHONY: help build test lint fmt-check check doc

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

build: ## Build nightshiftd and nightshift into bin/, stamped with VERSION
	go build -ldflags "$(LDFLAGS)" -o bin/ ./cmd/...

test: ## Run every test
	go test ./... -count=1

lint: ## Run go vet
	go vet ./...

fmt-check: ## Fail when a file is not gofmt-formatted
	@out=$$(gofmt -l .); if [ -n "$$out" ]; then echo "not gofmt-formatted:"; echo "$$out"; exit 1; fi

check: fmt-check lint test ## Check formatting, then lint, then test

# The programs under cmd/ are main packages and export nothing, so only
# library packages outside internal/ are documented; with none, doc writes
# nothing.
doc: ## Write the API reference of every package outside internal/ to docs/generated/
	@pkgs=$$(go list -f '{{if ne .Name "main"}}{{.ImportPath}}{{end}}' ./... | grep -v -e '/internal/' -e '/internal$$' || true); \
	for pkg in $$pkgs; do \
		mkdir -p docs/generated; \
		name=$$(echo "$${pkg#$(MODULE)/}" | tr / _); \
		go doc -all "$$pkg" > "docs/generated/$$name.txt" || exit 1; \
	done
