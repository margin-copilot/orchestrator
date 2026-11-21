"""D-011's rule for composed answers: every number comes from this turn's agent replies (or the
user's own question). Numbers compare by value, so "21.30%" is grounded by "21.3%"."""

import re
from decimal import Decimal

NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)")


def ungrounded_numbers(answer: str, sources: list[str]) -> list[str]:
    known = {Decimal(n) for source in sources for n in NUMBER.findall(source)}
    return [n for n in NUMBER.findall(answer) if Decimal(n) not in known]
