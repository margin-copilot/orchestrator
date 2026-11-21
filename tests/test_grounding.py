from orchestrator.domain.grounding import ungrounded_numbers


def test_numbers_match_by_value():
    assert ungrounded_numbers("Margin was 21.30%.", ["North dairy margin: 21.3%"]) == []


def test_numbers_in_the_question_count():
    sources = ["Was the North flour increase in February 2026 allowed?", "It rose on 12 Feb."]
    assert ungrounded_numbers("The 2026 increase on 12 Feb broke PP-07.", [*sources, "PP-07"]) == []


def test_an_invented_number_is_reported():
    replies = ["UHT milk cost rose 6.0% (PP-05-N §2).", "Tier B allows 4% (PP-04 §3.2)."]
    answer = "Raise UHT milk to 7.25 MAD, within 4% (PP-04 §3.2); costs rose 6%."
    assert ungrounded_numbers(answer, replies) == ["7.25"]


SOURCES = [
    "North flour rose from 35.00 to 36.00 MAD (+2.9%) on 2026-02-20 (PP-02 §1).",
    "Tier A prices can't rise during Ramadan (18 Feb to 19 Mar 2026). PP-07 §2",
]


def test_clause_numbers_never_ground_a_figure():
    assert ungrounded_numbers("It rose by 1.00 MAD.", SOURCES) == ["1.00"]
    assert ungrounded_numbers("It rose 2%.", SOURCES) == ["2%"]


def test_a_percentage_needs_a_percentage():
    assert ungrounded_numbers("Margin is 35.00%.", SOURCES) == ["35.00%"]
    assert ungrounded_numbers("It rose 2.9%.", SOURCES) == []


def test_thousands_stay_one_number():
    sources = ["Net sales were 5,066,877.50 MAD."]
    assert ungrounded_numbers("Sales: 5,066,877.50 MAD.", sources) == []
    assert ungrounded_numbers("Sales: 3,021 units.", ["3 stores and 21 days"]) == ["3,021"]


def test_clause_ids_must_come_from_the_replies():
    from orchestrator.domain.grounding import unknown_citations

    assert unknown_citations("Not allowed (PP-07 §2).", SOURCES) == []
    assert unknown_citations("Not allowed (PP-06 §1).", SOURCES) == ["PP-06 §1"]
