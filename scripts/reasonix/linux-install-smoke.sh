#!/usr/bin/env bash
set -euo pipefail
if [ "${GITHUB_ACTIONS:-}" != true ]; then
  echo "installed-package acceptance only runs in GitHub Actions (GITHUB_ACTIONS=true)" >&2
  exit 1
fi
deb=$(realpath -- "${1:?usage: linux-install-smoke.sh DEB ARCH}")
arch=${2:?usage: linux-install-smoke.sh DEB ARCH}
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
test "$(dpkg --print-architecture)" = "$arch"
test "$(dpkg-deb -f "$deb" Package)" = reasonix-studio
test "$(dpkg-deb -f "$deb" Architecture)" = "$arch"
if dpkg-query -W -f='${Status}' reasonix-studio 2>/dev/null | grep -qx 'install ok installed'; then
  echo "installed-package acceptance requires a disposable machine without Studio" >&2
  exit 1
fi
version=$(dpkg-deb -f "$deb" Version)
shell_version=${3:?usage: linux-install-smoke.sh DEB ARCH SHELL_VERSION}
stage=$(mktemp -d)
cleanup() {
  if dpkg-query -W -f='${Status}' reasonix-studio 2>/dev/null | grep -qx 'install ok installed'; then
    sudo apt-get purge -y reasonix-studio
  fi
  rm -rf -- "$stage"
}
trap cleanup EXIT
dpkg-deb -x "$deb" "$stage"
extracted=$(node "$root/desktop/electron/packaging/linux-tree.js" "$stage" "$arch")
shell=${extracted#"$stage"}
for attempt in install reinstall; do
  sudo apt-get install -y --reinstall "$deb"
  test "$(dpkg-query -W -f='${Version}' reasonix-studio)" = "$version"
  xvfb-run -a node "$root/desktop/electron/test/linux-package-smoke.js" "$shell" "$arch" "$shell_version"
done
sudo apt-get purge -y reasonix-studio
test ! -e "$shell"
test ! -e /usr/lib/reasonix-studio/reasonix-studio-update-helper
test ! -e /usr/share/polkit-1/actions/io.reasonix.studio.update.policy
