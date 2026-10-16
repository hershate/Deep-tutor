// Windows 11 still reports NT 10.0: only the build number tells it from
// Windows 10, and 22000 is where the 11 line starts. Server 2025 shares the
// 26100 build, so the name is a client-edition reading, not a product claim.
const WINDOWS_11_FIRST_BUILD = 22000;

export function systemLabel(os: string, osVersion: string, arch: string): string {
  const build = os === "windows" ? /^10\.0\.(\d+)$/.exec(osVersion)?.[1] : undefined;
  if (build !== undefined && Number(build) >= WINDOWS_11_FIRST_BUILD) {
    return [`Windows 11 (${osVersion})`, arch].filter(Boolean).join(" ");
  }
  return [os, osVersion, arch].filter(Boolean).join(" ");
}
