#!/bin/sh
# Rebuild the portfolio's CSS (templates/portfolio/static/portfolio.css) with Tailwind's
# standalone CLI, so no Node is needed. Run it from backend/ after changing the portfolio
# template. The CLI is downloaded once into .cache/, and its SHA-256 is checked first.
set -eu

VERSION=v4.3.3
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ASSET=tailwindcss-linux-x64
    SHA256=dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a ;;
  Linux-aarch64 | Linux-arm64) ASSET=tailwindcss-linux-arm64
    SHA256=55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195 ;;
  Darwin-arm64) ASSET=tailwindcss-macos-arm64
    SHA256=cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d ;;
  Darwin-x86_64) ASSET=tailwindcss-macos-x64
    SHA256=7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1 ;;
  *) echo "No Tailwind build for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

CLI=".cache/$ASSET-$VERSION"
if [ ! -x "$CLI" ]; then
  mkdir -p .cache
  curl -fsSL -o "$CLI.download" \
    "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/$ASSET"
  if command -v sha256sum > /dev/null; then
    echo "$SHA256  $CLI.download" | sha256sum -c - > /dev/null
  else
    echo "$SHA256  $CLI.download" | shasum -a 256 -c - > /dev/null
  fi
  chmod +x "$CLI.download"
  mv "$CLI.download" "$CLI"
fi

"$CLI" --input templates/portfolio/tailwind.css \
  --output templates/portfolio/static/portfolio.css --minify
