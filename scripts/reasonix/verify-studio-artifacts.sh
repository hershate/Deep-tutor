#!/usr/bin/env bash
set -euo pipefail
dir=${1:?usage: verify-studio-artifacts.sh DIRECTORY}
for arch in amd64 arm64; do
  for name in "ReasonixStudio-linux-$arch.deb" "ReasonixStudio-linux-$arch.rpm" \
    "ReasonixStudio-windows-$arch-installer.exe" "ReasonixStudio-windows-$arch.zip" \
    "ReasonixStudio-darwin-$arch.dmg" "ReasonixStudio-darwin-$arch.zip"; do
    test -s "$dir/$name" || { echo "missing or empty Studio artifact: $name" >&2; exit 1; }
  done
done
