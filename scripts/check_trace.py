#!/usr/bin/env python3
"""識別子の重複と、上下の対応を検査する。

PB → REQ → AC → INC の対応が切れていないこと、同じ番号が二つ定義されて
いないこと、番号だけで名前の無い定義が無いことを見る。
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

# 増分をまたいで生きる識別子は、種別と連番だけを持つ。
DEFINITION = re.compile(r"^\|\s*`(?P<id>(?:PB|SC|REQ|AC)-\d{3})`")
REFERENCE = re.compile(r"`((?:PB|SC|REQ|AC)-\d{3})`")
ADR_FILE = re.compile(r"^ADR-(\d{3})-")

# 一つの増分の中だけで使う識別子は、種別・Issue番号・その増分の中の連番を持つ。
INCREMENT_FILE = re.compile(r"^INC-(\d{3,})\.md$")
SCOPED = re.compile(r"`(?:IDEA|SPEC|ST|AD|IT)-(?P<issue>\d{3,})-\d{3}`")


def definitions(path: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    if not path.exists():
        return found
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = DEFINITION.match(line)
        if match:
            found[match.group("id")] = number
    return found


def references(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(REFERENCE.findall(path.read_text(encoding="utf-8")))


def scoped_to_issue(path: Path, issue: str) -> list[str]:
    """増分の中の識別子が、その増分の Issue 番号を挟んでいるかを見る。

    番号だけで所属が読めることがこの採番の目的なので、別の増分の番号が
    混ざると意味が無くなる。
    """
    wrong = {
        found
        for found in SCOPED.finditer(path.read_text(encoding="utf-8"))
        if found.group("issue") != issue
    }
    return [
        f"{path.name}: {found.group(0)} が別の増分の番号を挟んでいる（{issue} のはず）"
        for found in sorted(wrong, key=lambda m: m.group(0))
    ]


def paired(path: Path, upper: str, lower: str) -> list[str]:
    """増分の中で、上位の判断と、それを確かめるテストが対になっているかを見る。

    対応する ST が無い SPEC は、満たしたかを判定できない。上位を指さない ST は、
    作る理由が無い。AD と IT も同じである。
    """
    uppers: set[str] = set()
    lowers: dict[str, set[str]] = {}
    row = re.compile(r"^\|\s*`(?P<id>(?:" + upper + "|" + lower + r")-\d{3,}-\d{3})`")
    reference = re.compile(r"`(" + upper + r"-\d{3,}-\d{3})`")

    for line in path.read_text(encoding="utf-8").splitlines():
        match = row.match(line)
        if not match:
            continue
        identifier = match.group("id")
        if identifier.startswith(upper + "-"):
            uppers.add(identifier)
        else:
            lowers[identifier] = set(reference.findall(line))

    errors: list[str] = []
    for identifier, refs in sorted(lowers.items()):
        if not refs:
            errors.append(f"{path.name}: {identifier} が対応する {upper} を指していない")
        for ref in sorted(refs - uppers):
            errors.append(f"{path.name}: {identifier} が同じ増分に無い {ref} を指している")

    covered = {ref for refs in lowers.values() for ref in refs}
    for identifier in sorted(uppers - covered):
        errors.append(f"{path.name}: {identifier} を確かめる {lower} が無い")
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_ROOT
    requirements = root / "docs" / "110_requirements"
    increments = root / "docs" / "210_increments"
    decisions = root / "docs" / "010_decisions"

    errors: list[str] = []

    problems = definitions(requirements / "01-背景と課題.md")
    scenarios = definitions(requirements / "02-利用者とシナリオ.md")
    demands = definitions(requirements / "03-要求.md")
    criteria = definitions(requirements / "04-受入基準.md")

    # 02 はシナリオを見出しで定義する。
    scenario_heading = re.compile(r"^###\s+(SC-\d{3})\s+\S")
    scenario_file = requirements / "02-利用者とシナリオ.md"
    if scenario_file.exists():
        for number, line in enumerate(scenario_file.read_text(encoding="utf-8").splitlines(), 1):
            match = scenario_heading.match(line)
            if match:
                scenarios[match.group(1)] = number

    # 重複した定義
    seen: dict[str, list[str]] = defaultdict(list)
    for name, table in (
        ("01-背景と課題", problems),
        ("02-利用者とシナリオ", scenarios),
        ("03-要求", demands),
        ("04-受入基準", criteria),
    ):
        for identifier in table:
            seen[identifier].append(name)
    for identifier, places in sorted(seen.items()):
        if len(places) > 1:
            errors.append(f"{identifier} が複数の文書で定義されている: {', '.join(places)}")

    # 要求が指す課題は実在すること
    for identifier in references(requirements / "03-要求.md"):
        if identifier.startswith("PB-") and identifier not in problems:
            errors.append(f"03-要求 が存在しない {identifier} を指している")

    # 受入基準が指す要求と場面は実在すること
    for identifier in references(requirements / "04-受入基準.md"):
        if identifier.startswith("REQ-") and identifier not in demands:
            errors.append(f"04-受入基準 が存在しない {identifier} を指している")
        if identifier.startswith("SC-") and identifier not in scenarios:
            errors.append(f"04-受入基準 が存在しない {identifier} を指している")

    # すべての要求に受入基準があること
    covered = {i for i in references(requirements / "04-受入基準.md") if i.startswith("REQ-")}
    for identifier in sorted(set(demands) - covered):
        errors.append(f"{identifier} に対応する受入基準が無い")

    # すべての課題が、いずれかの要求から指されていること
    addressed = {i for i in references(requirements / "03-要求.md") if i.startswith("PB-")}
    for identifier in sorted(set(problems) - addressed):
        errors.append(f"{identifier} を扱う要求が無い")

    for path in sorted(increments.glob("INC-*.md")):
        name = INCREMENT_FILE.match(path.name)
        if not name:
            errors.append(f"{path.name} の名前が INC-<Issue番号>.md でない")
            continue

        # 増分が指す受入基準は実在すること
        for identifier in references(path):
            if identifier.startswith("AC-") and identifier not in criteria:
                errors.append(f"{path.name} が存在しない {identifier} を指している")

        # 増分の中の識別子が、その増分の Issue 番号を挟んでいること
        errors.extend(scoped_to_issue(path, name.group(1)))

        # 増分の中で、要件とテストが対になっていること
        errors.extend(paired(path, upper="SPEC", lower="ST"))
        errors.extend(paired(path, upper="AD", lower="IT"))

    # ADR の番号が重複していないこと
    adr_numbers: dict[str, str] = {}
    for path in sorted(decisions.glob("ADR-*.md")):
        match = ADR_FILE.match(path.name)
        if not match:
            errors.append(f"{path.name} の名前が ADR-nnn-<意味の分かる名前>.md でない")
            continue
        number = match.group(1)
        if number in adr_numbers:
            errors.append(f"ADR-{number} が重複している: {adr_numbers[number]}, {path.name}")
        adr_numbers[number] = path.name

    if errors:
        print("識別子の対応に問題があります:", file=sys.stderr)
        for line in errors:
            print(f"  {line}", file=sys.stderr)
        return 1

    increment_count = len(list(increments.glob("INC-*.md")))
    print(
        "識別子の対応: 問題なし "
        f"(PB {len(problems)} / SC {len(scenarios)} / REQ {len(demands)} "
        f"/ AC {len(criteria)} / ADR {len(adr_numbers)} / INC {increment_count})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
