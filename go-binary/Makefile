# Cross-compile a single static binary per OS/arch with no runtime deps.
# All builds are CGO-disabled so glibc/musl/dynamic linker mismatches can't
# bite the user on a fresh machine.

BINARY        := gcp-appliance-status
PKG           := github.com/webgress/gcp_bulk_user/go-binary
HOST_BIN      := bin/$(BINARY)
DIST_DIR      := dist

GOFLAGS       := -trimpath
LDFLAGS       := -s -w
BUILD_ENV     := CGO_ENABLED=0

# Cross-compile matrix. SPEC: darwin-arm64, darwin-amd64, linux-amd64,
# linux-arm64, windows-amd64.
PLATFORMS := \
	darwin/arm64 \
	darwin/amd64 \
	linux/amd64 \
	linux/arm64 \
	windows/amd64

.PHONY: build release clean test help

help:
	@echo "Targets:"
	@echo "  build    - build for the host into $(HOST_BIN)"
	@echo "  release  - cross-compile every supported OS/arch into $(DIST_DIR)/"
	@echo "  test     - run unit tests"
	@echo "  clean    - remove build artifacts"

build:
	@mkdir -p bin
	$(BUILD_ENV) go build $(GOFLAGS) -ldflags="$(LDFLAGS)" -o $(HOST_BIN) .
	@ls -lh $(HOST_BIN)

release: clean-dist
	@mkdir -p $(DIST_DIR)
	@for plat in $(PLATFORMS); do \
		os=$${plat%/*}; arch=$${plat#*/}; \
		ext=""; \
		if [ "$$os" = "windows" ]; then ext=".exe"; fi; \
		out="$(DIST_DIR)/$(BINARY)-$$os-$$arch$$ext"; \
		echo "==> $$os/$$arch -> $$out"; \
		GOOS=$$os GOARCH=$$arch $(BUILD_ENV) go build $(GOFLAGS) \
			-ldflags="$(LDFLAGS)" -o $$out . || exit 1; \
	done
	@echo
	@ls -lh $(DIST_DIR)

test:
	go test ./...

clean: clean-dist
	rm -rf bin

clean-dist:
	rm -rf $(DIST_DIR)
