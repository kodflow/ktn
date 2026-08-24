#!/bin/bash

# KTN-Linter Universal Installer
# Installs ktn-linter from the public distribution repository releases.
# Can be used on any Go project.

set -euo pipefail

# The binaries are distributed from a public mirror; the source repository is
# private, so no fallback to a source build is possible here.
REPO="${KTN_REPO:-kodflow/ktn}"
BINARY_NAME="ktn-linter"
VERSION="${KTN_VERSION:-latest}"
CHECKSUM_FILE="checksums.txt"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   KTN-Linter Universal Installer      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

die() {
    echo -e "${RED}❌ $1${NC}" >&2
    exit 1
}

# ─────────────────────────────────────────────────────────────
# Platform detection
#
# Release assets are named `ktn-linter_<os>_<arch>.<ext>` (underscores, and an
# archive rather than a bare binary). Any other convention resolves to nothing.
# ─────────────────────────────────────────────────────────────
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)

case "$ARCH" in
    x86_64|amd64)   ARCH="amd64" ;;
    aarch64|arm64)  ARCH="arm64" ;;
    *)              die "Unsupported architecture: $ARCH" ;;
esac

case "$OS" in
    linux|darwin)
        ARCHIVE_EXT="tar.gz"
        ;;
    msys*|mingw*|cygwin*|windows*)
        OS="windows"
        ARCHIVE_EXT="zip"
        BINARY_NAME="ktn-linter.exe"
        ;;
    *)
        die "Unsupported OS: $OS"
        ;;
esac

# Only linux/darwin ship an arm64 build; fail before downloading a 404 page.
if [ "$OS" = "windows" ] && [ "$ARCH" != "amd64" ]; then
    die "Unsupported platform: ${OS}/${ARCH}"
fi

ASSET_NAME="ktn-linter_${OS}_${ARCH}.${ARCHIVE_EXT}"

echo -e "${YELLOW}📦 Platform: ${OS}/${ARCH} (${ASSET_NAME})${NC}"

# ─────────────────────────────────────────────────────────────
# Installation directory
# ─────────────────────────────────────────────────────────────
if [ -w "/usr/local/bin" ]; then
    INSTALL_DIR="/usr/local/bin"
elif [ -n "${HOME:-}" ]; then
    INSTALL_DIR="$HOME/.local/bin"
    mkdir -p "$INSTALL_DIR"

    # Add to PATH if not already there
    if [[ ":${PATH:-}:" != *":$INSTALL_DIR:"* ]]; then
        echo -e "${YELLOW}💡 Adding $INSTALL_DIR to PATH${NC}"
        echo ""
        echo -e "${YELLOW}Add this to your shell profile (~/.bashrc, ~/.zshrc, etc.):${NC}"
        echo -e "${GREEN}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
        echo ""
    fi
else
    die "Cannot determine installation directory"
fi

echo -e "${YELLOW}📂 Installation directory: $INSTALL_DIR${NC}"

# ─────────────────────────────────────────────────────────────
# Version resolution
#
# `releases/latest` is resolved through its HTTP redirect rather than the REST
# API: the API is rate limited to 60 anonymous calls per hour and per IP, which
# a CI runner exhausts quickly.
# ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}🔍 Fetching release information...${NC}"

if [ "$VERSION" = "latest" ]; then
    EFFECTIVE_URL=$(curl -fsSL --retry 3 --proto '=https' -o /dev/null \
        -w '%{url_effective}' "https://github.com/${REPO}/releases/latest") \
        || die "Cannot reach https://github.com/${REPO}/releases/latest"
    VERSION="${EFFECTIVE_URL##*/}"
fi

case "$VERSION" in
    ""|*/*|latest)
        die "Cannot resolve a release tag for ${REPO}"
        ;;
esac

echo -e "${GREEN}✅ Found version: ${VERSION}${NC}"

BASE_URL="https://github.com/${REPO}/releases/download/${VERSION}"

# ─────────────────────────────────────────────────────────────
# Download
# ─────────────────────────────────────────────────────────────
TEMP_DIR=$(mktemp -d)
# The temp dir holds the archive and the extracted binary; drop it on every
# exit path, including the failures above the install step.
# STAGED_BINARY is created later, inside INSTALL_DIR rather than TEMP_DIR (the
# final step must be a same-filesystem rename). An interrupt between its mktemp
# and the mv would otherwise strand an executable next to the real binary, so
# the trap clears it too. After a successful mv the pathname is already gone,
# which makes the rm a harmless no-op.
STAGED_BINARY=""
trap 'rm -rf "$TEMP_DIR"; [ -n "$STAGED_BINARY" ] && rm -f "$STAGED_BINARY"' EXIT

echo -e "${YELLOW}⬇️  Downloading ${ASSET_NAME}...${NC}"
curl -fsSL --retry 3 --proto '=https' -o "${TEMP_DIR}/${ASSET_NAME}" \
    "${BASE_URL}/${ASSET_NAME}" \
    || die "Download failed: ${BASE_URL}/${ASSET_NAME}"

echo -e "${YELLOW}⬇️  Downloading ${CHECKSUM_FILE}...${NC}"
curl -fsSL --retry 3 --proto '=https' -o "${TEMP_DIR}/${CHECKSUM_FILE}" \
    "${BASE_URL}/${CHECKSUM_FILE}" \
    || die "Download failed: ${BASE_URL}/${CHECKSUM_FILE}"

# ─────────────────────────────────────────────────────────────
# Checksum verification — before extraction, never after: extracting an
# unverified archive is what the verification is meant to prevent.
# ─────────────────────────────────────────────────────────────
EXPECTED_SHA=$(awk -v name="$ASSET_NAME" '$2 == name || $2 == "*" name { print $1 }' \
    "${TEMP_DIR}/${CHECKSUM_FILE}" | head -n 1)

if [ -z "$EXPECTED_SHA" ]; then
    die "${ASSET_NAME} has no entry in ${CHECKSUM_FILE}"
fi

if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA=$(sha256sum "${TEMP_DIR}/${ASSET_NAME}" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA=$(shasum -a 256 "${TEMP_DIR}/${ASSET_NAME}" | awk '{print $1}')
elif command -v openssl >/dev/null 2>&1; then
    ACTUAL_SHA=$(openssl dgst -sha256 "${TEMP_DIR}/${ASSET_NAME}" | awk '{print $NF}')
else
    # Installing an unverified binary is worse than not installing one.
    die "No SHA-256 tool found (sha256sum, shasum or openssl required)"
fi

if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    echo -e "${RED}   expected: ${EXPECTED_SHA}${NC}" >&2
    echo -e "${RED}   actual:   ${ACTUAL_SHA}${NC}" >&2
    die "Checksum mismatch for ${ASSET_NAME}"
fi

echo -e "${GREEN}✅ Checksum verified (sha256)${NC}"

# ─────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────
EXTRACT_DIR="${TEMP_DIR}/extract"
mkdir -p "$EXTRACT_DIR"

case "$ARCHIVE_EXT" in
    tar.gz)
        tar -xzf "${TEMP_DIR}/${ASSET_NAME}" -C "$EXTRACT_DIR" \
            || die "Failed to extract ${ASSET_NAME}"
        ;;
    zip)
        command -v unzip >/dev/null 2>&1 || die "unzip is required to install ${ASSET_NAME}"
        unzip -q -o "${TEMP_DIR}/${ASSET_NAME}" -d "$EXTRACT_DIR" \
            || die "Failed to extract ${ASSET_NAME}"
        ;;
esac

# The archive holds the binary at its root, but tolerate a nested layout so a
# future change to the packaging does not silently break the installer.
EXTRACTED_BINARY="${EXTRACT_DIR}/${BINARY_NAME}"
if [ ! -f "$EXTRACTED_BINARY" ]; then
    EXTRACTED_BINARY=$(find "$EXTRACT_DIR" -type f -name "$BINARY_NAME" | head -n 1)
fi

if [ -z "$EXTRACTED_BINARY" ] || [ ! -f "$EXTRACTED_BINARY" ]; then
    die "${BINARY_NAME} not found inside ${ASSET_NAME}"
fi

# ─────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────
chmod +x "$EXTRACTED_BINARY"
# Stage inside INSTALL_DIR so the final step is a same-filesystem rename:
# an interrupted install then leaves the previous binary intact instead of a
# truncated one. Replacing a running binary in place fails on Linux (ETXTBSY),
# and rename() over a busy target does not, which is the other reason to stage.
STAGED_BINARY=$(mktemp "${INSTALL_DIR}/${BINARY_NAME}.XXXXXX") \
    || die "Cannot stage into ${INSTALL_DIR}"
cp "$EXTRACTED_BINARY" "$STAGED_BINARY" \
    || die "Cannot stage into ${INSTALL_DIR}"
chmod 0755 "$STAGED_BINARY"
mv -f "$STAGED_BINARY" "${INSTALL_DIR}/${BINARY_NAME}" \
    || die "Cannot install to ${INSTALL_DIR}"

echo -e "${GREEN}✅ Installed to ${INSTALL_DIR}/${BINARY_NAME}${NC}"

# Verify the binary we just wrote, never whatever `command -v` resolves: an
# older ktn-linter earlier in PATH would otherwise report a success that says
# nothing about this install.
BINARY_PATH="${INSTALL_DIR}/${BINARY_NAME}"
VERSION_OUTPUT=$("$BINARY_PATH" version 2>&1) || die "Installed binary is not runnable"
echo -e "${GREEN}✅ Installation verified: ${VERSION_OUTPUT}${NC}"

if ! command -v "$BINARY_NAME" >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Binary installed but not in PATH${NC}"
    echo -e "${YELLOW}   Run: export PATH=\"$INSTALL_DIR:\$PATH\"${NC}"
fi

# ─────────────────────────────────────────────────────────────
# MCP Configuration (Auto-inject ktn-linter into mcp.json)
# Supports both "mcpServers" and "servers" top-level keys.
# ─────────────────────────────────────────────────────────────
configure_mcp() {
    local mcp_file="$1"
    local binary="$2"

    [ -f "$mcp_file" ] || return 1

    if ! command -v jq &> /dev/null; then
        echo -e "${YELLOW}⚠️  jq not found, skipping MCP configuration for $mcp_file${NC}"
        return 1
    fi

    if ! jq empty "$mcp_file" 2>/dev/null; then
        echo -e "${YELLOW}⚠️  $mcp_file is not valid JSON, skipping${NC}"
        return 1
    fi

    # Detect which top-level key the file uses
    local server_key=""
    if jq -e '.mcpServers' "$mcp_file" >/dev/null 2>&1; then
        server_key="mcpServers"
    elif jq -e '.servers' "$mcp_file" >/dev/null 2>&1; then
        server_key="servers"
    else
        # No server key exists yet — use mcpServers as default
        server_key="mcpServers"
    fi

    # Skip if ktn-linter is already configured
    if jq -e --arg k "$server_key" '.[$k]["ktn-linter"]' "$mcp_file" >/dev/null 2>&1; then
        echo -e "${GREEN}✅ ktn-linter already in $mcp_file ($server_key)${NC}"
        return 0
    fi

    # Build the ktn-linter MCP server entry
    local ktn_config
    ktn_config=$(jq -n --arg cmd "$binary" '{
        command: $cmd,
        args: ["serve", "--port", "7717"],
        env: {}
    }')

    # Inject into the file
    local tmp_file
    tmp_file=$(mktemp "${mcp_file}.tmp.XXXXXX") || return 1

    if jq --arg k "$server_key" --argjson cfg "$ktn_config" \
       '(.[$k] // {}) as $s | .[$k] = ($s + {"ktn-linter": $cfg})' \
       "$mcp_file" > "$tmp_file" && jq empty "$tmp_file" 2>/dev/null; then
        mv "$tmp_file" "$mcp_file"
        echo -e "${GREEN}✅ ktn-linter added to $mcp_file ($server_key)${NC}"
    else
        rm -f "$tmp_file"
        echo -e "${RED}❌ Failed to inject ktn-linter into $mcp_file${NC}"
        return 1
    fi
}

echo ""
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  MCP Configuration (Auto-detect)     ${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo ""

MCP_CONFIGURED=false
for candidate in "./mcp.json" "./.vscode/mcp.json"; do
    if [ -f "$candidate" ]; then
        if configure_mcp "$candidate" "$BINARY_PATH"; then
            MCP_CONFIGURED=true
        fi
    fi
done

if [ "$MCP_CONFIGURED" = false ]; then
    echo -e "${YELLOW}💡 No mcp.json found in current directory${NC}"
    echo -e "${YELLOW}   To add ktn-linter as an MCP server, add this to your mcp.json:${NC}"
    echo ""
    echo -e "${GREEN}  {${NC}"
    echo -e "${GREEN}    \"mcpServers\": {${NC}"
    echo -e "${GREEN}      \"ktn-linter\": {${NC}"
    echo -e "${GREEN}        \"command\": \"$BINARY_PATH\",${NC}"
    echo -e "${GREEN}        \"args\": [\"serve\", \"--port\", \"7717\"],${NC}"
    echo -e "${GREEN}        \"env\": {}${NC}"
    echo -e "${GREEN}      }${NC}"
    echo -e "${GREEN}    }${NC}"
    echo -e "${GREEN}  }${NC}"
    echo ""
fi

echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Installation Complete! 🎉           ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Usage:${NC}"
echo -e "  ${YELLOW}ktn-linter run ./...${NC}            # Lint your project"
echo -e "  ${YELLOW}ktn-linter run --help${NC}           # Show help"
echo -e "  ${YELLOW}make lint${NC}                       # If Makefile configured"
echo ""
echo -e "${GREEN}Documentation:${NC}"
echo -e "  ${BLUE}https://github.com/${REPO}${NC}"
echo ""
