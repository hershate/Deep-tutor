#!/usr/bin/env bash
# Withdraw a published release from the rollback index, or put it back.
#
# A withdrawn release leaves "versions" — the only list a client that predates
# the field reads, so those clients stop being offered it — and moves into
# "withdrawn" with its whole row and a reason, which is what lets a newer client
# say why and what restoring needs. Usage:
#
#   withdraw-version.sh <index> <version> <reason> <at>
#   withdraw-version.sh <index> --restore <version> <at>
#
# The updated index is printed; the caller uploads it.
set -euo pipefail

restore=false
if [ "${2:-}" = "--restore" ]; then
  restore=true
  if [ "$#" -ne 4 ]; then
    echo "usage: $0 <index> --restore <version> <at>" >&2
    exit 2
  fi
  index="$1" version="$3" at="$4" reason=""
else
  if [ "$#" -ne 4 ]; then
    echo "usage: $0 <index> <version> <reason> <at>" >&2
    exit 2
  fi
  index="$1" version="$2" reason="$3" at="$4"
fi
version="${version#v}"

if [ ! -f "$index" ] || ! jq -e '.versions | type == "array"' "$index" >/dev/null; then
  echo "::error::$index is not a versions index" >&2
  exit 1
fi
if [ -z "$version" ] || [ -z "$at" ]; then
  echo "::error::a version and a timestamp are required" >&2
  exit 1
fi
if [ "$restore" = false ] && [ -z "${reason// }" ]; then
  echo "::error::withdrawing a release needs a reason" >&2
  exit 1
fi

jq --arg v "$version" --arg reason "$reason" --arg at "$at" --argjson restore "$restore" '
  def same(x): ((.version | ltrimstr("v")) == x);
  (.withdrawn // []) as $gone
  | if $restore then
      if ($gone | map(select(same($v))) | length) != 1 then error("version \($v) is not withdrawn") else . end
      | def semver: [splits("[.-]")] | map(select(test("^[0-9]+$")) | tonumber);
        .versions = ((.versions + [$gone[] | select(same($v)) | del(.reason, .withdrawnAt)])
                     | sort_by(.version | semver) | reverse)
        | .withdrawn = [$gone[] | select(same($v) | not)]
        | .updatedAt = $at
    else
      if ($gone | map(select(same($v))) | length) > 0 then error("version \($v) is already withdrawn") else . end
      | if (.versions | map(select(same($v))) | length) != 1 then error("version \($v) is not in the index") else . end
      | if (.versions | length) < 2 then error("the last remaining release cannot be withdrawn") else . end
      | .withdrawn = ($gone + [.versions[] | select(same($v)) + {reason: $reason, withdrawnAt: $at}])
        | .versions = [.versions[] | select(same($v) | not)]
        | .updatedAt = $at
    end
  | if (.withdrawn | length) == 0 then del(.withdrawn) else . end
' "$index"
