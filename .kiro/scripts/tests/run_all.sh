#!/usr/bin/env bash
# Run every test file in the workspace. `unittest discover` can't be used because the
# test directories have no __init__.py, so each file is executed directly.
#
# Usage: .kiro/scripts/tests/run_all.sh [-q]
#   -q  only print the per-file summary lines and the total
set -uo pipefail

cd "$(dirname "$0")/../../.." || exit 1

quiet=0
[[ "${1:-}" == "-q" ]] && quiet=1

files=(
  .kiro/scripts/tests/test_*.py
  .kiro/skills/simplify-tracker-sync/tests/test_*.py
)

total=0
failed_files=()

for f in "${files[@]}"; do
  [[ -e "$f" ]] || continue
  out=$(python3 "$f" 2>&1)
  rc=$?
  # "Ran N tests" is unittest's own count
  n=$(printf '%s\n' "$out" | sed -n 's/^Ran \([0-9]\+\) test.*/\1/p' | tail -1)
  n=${n:-0}
  total=$((total + n))
  if [[ $rc -eq 0 ]]; then
    printf 'PASS  %-64s %3s tests\n' "$f" "$n"
  else
    printf 'FAIL  %-64s %3s tests\n' "$f" "$n"
    failed_files+=("$f")
    [[ $quiet -eq 0 ]] && printf '%s\n' "$out" | grep -E '^(FAIL|ERROR):|^AssertionError|^[A-Za-z]*Error:' | sed 's/^/        /'
  fi
done

echo "------------------------------------------------------------------------------------"
if [[ ${#failed_files[@]} -eq 0 ]]; then
  echo "ALL GREEN — $total tests across ${#files[@]} files"
  exit 0
fi
echo "$total tests run; ${#failed_files[@]} file(s) failed:"
printf '  %s\n' "${failed_files[@]}"
exit 1
