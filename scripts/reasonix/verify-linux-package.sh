#!/usr/bin/env bash
set -euo pipefail

deb=${1:?usage: verify-linux-package.sh DEB RPM ARCH}
rpm=${2:?usage: verify-linux-package.sh DEB RPM ARCH}
arch=${3:?usage: verify-linux-package.sh DEB RPM ARCH}
case "$arch" in
  amd64) rpm_arch=x86_64 ;;
  arm64) rpm_arch=aarch64 ;;
  *) echo "unsupported architecture: $arch" >&2; exit 1 ;;
esac
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT

test "$(dpkg-deb -f "$deb" Package)" = reasonix-studio
test "$(dpkg-deb -f "$deb" Architecture)" = "$arch"
test "$(rpm -qp --qf '%{NAME}' "$rpm")" = reasonix-studio
test "$(rpm -qp --qf '%{ARCH}' "$rpm")" = "$rpm_arch"

dpkg-deb -c "$deb" > "$stage/deb-contents"
grep -qE '^-rwxr-xr-x (root/root|0/0) +[0-9]+ .* \./usr/lib/reasonix-studio/reasonix-studio-update-helper$' "$stage/deb-contents"
grep -qE '^-rw-r--r-- (root/root|0/0) +[0-9]+ .* \./usr/share/polkit-1/actions/io.reasonix.studio.update.policy$' "$stage/deb-contents"
rpm -qp --qf '[%{FILEMODES:perms} %{FILEUSERNAME} %{FILEGROUPNAME} %{FILENAMES}\n]' "$rpm" > "$stage/rpm-contents"
grep -qx -- '-rwxr-xr-x root root /usr/lib/reasonix-studio/reasonix-studio-update-helper' "$stage/rpm-contents"
grep -qx -- '-rw-r--r-- root root /usr/share/polkit-1/actions/io.reasonix.studio.update.policy' "$stage/rpm-contents"

dpkg-deb -x "$deb" "$stage/deb"
mkdir "$stage/rpm"
rpm2cpio "$rpm" > "$stage/payload.cpio"
(cd "$stage/rpm" && cpio -idm --quiet --no-absolute-filenames < "$stage/payload.cpio")
for format in deb rpm; do
  node "$root/desktop/electron/packaging/linux-tree.js" "$stage/$format" "$arch"
  test "$("$stage/$format/usr/lib/reasonix-studio/reasonix-studio-update-helper" package)" = reasonix-studio
done
