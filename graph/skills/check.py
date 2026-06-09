"""CLI for invoking skills and asserting their output without an LLM or graph.

Usage
-----
  python3 -m graph.skills.check                            list all skills
  python3 -m graph.skills.check <name> '<json>'            invoke, print result
  python3 -m graph.skills.check <name> '<json>' [ASSERT]   invoke + assert
  python3 -m graph.skills.check --run-tests                run all SKILL_TESTS

Assert flags (combinable)
  --assert-type   list|dict|str|int|float|bool
  --assert-key    KEY          key must exist in a dict result
  --assert-contains SUBSTR     substr must appear in str(result)
  --assert-nonempty            result must be truthy (non-empty list/dict/str)

Exit code 0 = pass, 1 = assertion failure or invocation error.
"""

from __future__ import annotations

import json
import pprint
import sys
from typing import Any

from graph.skills.loader import get_loader

_TYPE_MAP = {
    "list": list,
    "dict": dict,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}


def _assert(result: Any, args: list[str]) -> list[str]:
    """Return a list of failure messages (empty = all pass)."""
    failures: list[str] = []

    i = 0
    while i < len(args):
        flag = args[i]
        if flag == "--assert-type":
            expected = args[i + 1]
            t = _TYPE_MAP.get(expected)
            if t is None:
                failures.append(f"unknown type '{expected}'; choose from {list(_TYPE_MAP)}")
            elif not isinstance(result, t):
                failures.append(f"--assert-type {expected}: got {type(result).__name__}")
            i += 2
        elif flag == "--assert-key":
            key = args[i + 1]
            if not isinstance(result, dict) or key not in result:
                failures.append(f"--assert-key {key!r}: key missing (result type: {type(result).__name__})")
            i += 2
        elif flag == "--assert-contains":
            substr = args[i + 1]
            if substr not in str(result):
                failures.append(f"--assert-contains {substr!r}: not found in result")
            i += 2
        elif flag == "--assert-nonempty":
            if not result:
                failures.append(f"--assert-nonempty: result is falsy / empty")
            i += 1
        else:
            i += 1

    return failures


def _run_check(name: str, inputs: dict, assert_args: list[str]) -> bool:
    loader = get_loader()
    try:
        tool = loader.get(name)
    except KeyError:
        print(f"ERROR: unknown skill '{name}'")
        print(f"Available: {', '.join(sorted(loader.names()))}")
        return False

    print(f"Invoking '{name}' with {inputs} ...")
    try:
        result = tool.invoke(inputs)
    except Exception as exc:
        print(f"ERROR during invocation: {exc}")
        return False

    print("Result:")
    pprint.pprint(result)

    if assert_args:
        failures = _assert(result, assert_args)
        if failures:
            print("\nASSERTION FAILURES:")
            for f in failures:
                print(f"  FAIL  {f}")
            return False
        print("\nAll assertions PASSED")

    return True


def _run_tests() -> bool:
    loader = get_loader()
    all_tests = loader.tests()
    if not all_tests:
        print("No SKILL_TESTS defined in any tool module.")
        return True

    passed = skipped = failed = 0
    for tc in all_tests:
        name = tc["tool"]
        desc = tc.get("description", "")
        if tc.get("skip"):
            print(f"  SKIP  {name}  ({desc})")
            skipped += 1
            continue

        inputs = tc["input"]
        print(f"  RUN   {name}  ({desc})")
        try:
            result = loader.invoke(name, inputs)
        except Exception as exc:
            print(f"        ERROR: {exc}")
            failed += 1
            continue

        assert_args: list[str] = []
        if "assert_type" in tc:
            assert_args += ["--assert-type", tc["assert_type"].__name__]
        if "assert_key" in tc:
            assert_args += ["--assert-key", tc["assert_key"]]
        if "assert_contains" in tc:
            assert_args += ["--assert-contains", tc["assert_contains"]]
        if tc.get("assert_nonempty"):
            assert_args.append("--assert-nonempty")

        failures = _assert(result, assert_args)
        if failures:
            for f in failures:
                print(f"        FAIL  {f}")
            failed += 1
        else:
            print(f"        PASS")
            passed += 1

    total = passed + failed + skipped
    print(f"\n{total} tests: {passed} passed, {failed} failed, {skipped} skipped")
    return failed == 0


def main() -> None:
    args = sys.argv[1:]

    if not args:
        loader = get_loader()
        print(f"{'Skill':<35} Description")
        print("-" * 75)
        for t in sorted(loader.all(), key=lambda t: t.name):
            print(f"{t.name:<35} {t.description}")
        return

    if args[0] == "--run-tests":
        ok = _run_tests()
        sys.exit(0 if ok else 1)

    name = args[0]
    if len(args) < 2:
        print(f"Usage: python3 -m graph.skills.check <name> '<json>' [--assert-*]")
        sys.exit(1)

    try:
        inputs = json.loads(args[1])
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON input: {exc}")
        sys.exit(1)

    assert_args = args[2:]
    ok = _run_check(name, inputs, assert_args)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
