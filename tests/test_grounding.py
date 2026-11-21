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
