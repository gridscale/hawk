.PHONY: all clean package deb

# Detect fpm binary - ensure it's the Ruby fpm (Effing Package Management), not Fortran fpm
FPM := $(shell \
	for fpm in $$(which fpm 2>/dev/null) \
		$$(gem environment | grep 'EXECUTABLE DIRECTORY' | cut -d: -f2 | xargs)/fpm \
		$(HOME)/.gem/ruby/*/bin/fpm \
		/home/linuxbrew/.linuxbrew/bin/fpm \
		/opt/homebrew/bin/fpm \
		/usr/local/bin/fpm; do \
		if [ -x "$$fpm" ] && "$$fpm" --version 2>&1 | grep -q "^[0-9]"; then \
			echo "$$fpm"; \
			break; \
		fi; \
	done)

ifeq ($(FPM),)
$(error fpm (Effing Package Management) not found. Install with: gem install fpm)
endif

PYTHON := python3
PACKAGE_NAME := gs-hawk
VERSION := $(shell git describe --tags --always --dirty 2>/dev/null || echo "0.0.0")
MAINTAINER := hendrik@gridscale.io
DESCRIPTION := gridscale Hawk

# Package metadata
DEB_DEPENDENCIES := libcap2-bin, ieee-data, systemd (>= 249.0), python3-jinja2, python3-debian, python3-yaml, python3-prometheus-client, python3-netaddr, python3-cryptography, python3-pyroute2
DEB_RECOMMENDS := 1password-cli

# Directories
SRC_DIR := src
BIN_DIR := bin
PACKAGING_DIR := pkg
BUILD_DIR := build/package
PKG_PREFIX := usr
PKG_LIB_DIR := $(BUILD_DIR)/$(PKG_PREFIX)/lib/gs-hawk
PKG_BIN_DIR := $(BUILD_DIR)/$(PKG_PREFIX)/bin
PKG_SYSTEMD_DIR := $(BUILD_DIR)/$(PKG_PREFIX)/lib/systemd/system
PKG_VAR_DIR := $(BUILD_DIR)/var/lib/gs-hawk
PKG_SHARE_DIR := $(BUILD_DIR)/$(PKG_PREFIX)/share/gs-hawk

# Output directory
OUTPUT_DIR := dist

all: deb

$(BUILD_DIR):
	@mkdir -p $(BUILD_DIR)

$(OUTPUT_DIR):
	@mkdir -p $(OUTPUT_DIR)

package: $(BUILD_DIR)
	@echo "Preparing package structure..."
	@# Copy Python source files
	@rsync -av --exclude='__pycache__' --exclude='*.pyc' $(SRC_DIR)/ $(PKG_LIB_DIR)/
	@# Copy binaries
	@mkdir -p $(PKG_BIN_DIR)
	@cp $(BIN_DIR)/hawk $(PKG_BIN_DIR)/
	@cp $(BIN_DIR)/hawk-crypt $(PKG_BIN_DIR)/
	@# Copy systemd helper units
	@mkdir -p $(PKG_SYSTEMD_DIR)
	@cp $(PACKAGING_DIR)/usr/lib/systemd/system/hawk-unit-restarter@.service $(PKG_SYSTEMD_DIR)/
	@cp $(PACKAGING_DIR)/usr/lib/systemd/system/hawk-unit-reloader@.service $(PKG_SYSTEMD_DIR)/
	@# Copy feathers (example plugins and test feathers)
	@mkdir -p $(PKG_SHARE_DIR)
	@# Create var directory
	@mkdir -p $(PKG_VAR_DIR)
	@echo "Package structure created at $(BUILD_DIR)"

deb: package $(OUTPUT_DIR)
	@echo "Building Debian package..."
	$(FPM) \
		-s dir \
		-t deb \
		-n $(PACKAGE_NAME) \
		-v $(VERSION) \
		--maintainer "$(MAINTAINER)" \
		--description "$(DESCRIPTION)" \
		--architecture all \
		--epoch 2 \
		--deb-pre-depends "libcap2-bin" \
		-d "$(DEB_DEPENDENCIES)" \
		--deb-recommends "$(DEB_RECOMMENDS)" \
		--prefix / \
		-C $(BUILD_DIR) \
		-p $(OUTPUT_DIR)/$(PACKAGE_NAME)_$(VERSION)_all.deb \
		.

clean:
	@echo "Cleaning build artifacts..."
	@rm -rf $(BUILD_DIR)
	@rm -rf $(OUTPUT_DIR)
	@echo "Clean complete"
