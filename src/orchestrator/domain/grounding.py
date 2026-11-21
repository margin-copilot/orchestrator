"""D-011's rule for composed answers: every number comes from this turn's agent replies or from
what the user typed. Numbers compare by value ("21.30%" is grounded by "21.3%"); a percentage
needs a percentage; "5,066,877.50" is one number. Clause IDs are checked as IDs, never as
numbers, so "§2" can't ground "2%". Known gaps: number words, and date parts reused as figures."""

import re
from decimal import Decimal

CITATION = re.compile(r"PP-\d{2}(?:-[NCS])? §\d+(?:\.\d+)?")
NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(\s?%)?")


def _numbers(text: str) -> list[tuple[str, Decimal, bool]]:
    found = NUMBER.finditer(CITATION.sub(" ", text))
    return [(m.group(0), Decimal(m.group(1).replace(",", "")), bool(m.group(2))) for m in found]


def ungrounded_numbers(answer: str, sources: list[str]) -> list[str]:
    known = {(value, percent) for source in sources for _, value, percent in _numbers(source)}
    values = {value for value, _ in known}
    return [
        written
        for written, value, percent in _numbers(answer)
        if not ((value, True) in known if percent else value in values)
    ]


def unknown_citations(answer: str, sources: list[str]) -> list[str]:
    known = {citation for source in sources for citation in CITATION.findall(source)}
    return [citation for citation in CITATION.findall(answer) if citation not in known]
