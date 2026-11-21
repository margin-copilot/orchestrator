from margin_copilot_shared.contracts import AgentReply

from orchestrator.domain.replies import combined_citations, combined_refusal, stitched


def test_a_partial_refusal_keeps_the_answer():
    replies = [AgentReply(answer="no", refusal="no_access"), AgentReply(answer="Tier B: 4%.")]
    assert combined_refusal(replies) is None


def test_all_agents_refusing_is_a_refusal():
    replies = [
        AgentReply(answer="x", refusal="no_data"),
        AgentReply(answer="y", refusal="no_access"),
    ]
    assert combined_refusal(replies) == "no_access"
    assert combined_refusal([AgentReply(answer="x", refusal="no_data")] * 2) == "no_data"


def test_citations_are_merged_in_order_without_duplicates():
    replies = [
        AgentReply(answer="a", citations=["PP-04 §3.2", "PP-02 §2"]),
        AgentReply(answer="b", citations=["PP-02 §2", "PP-07 §2"]),
    ]
    assert combined_citations(replies) == ("PP-04 §3.2", "PP-02 §2", "PP-07 §2")


def test_stitched_replies_name_their_source():
    named = [
        ("data-agent", AgentReply(answer="Flour rose 2.9% on 12 Feb.")),
        ("policy-agent", AgentReply(answer="Tier A can't rise in Ramadan.")),
    ]
    assert stitched(named) == (
        "From the sales data: Flour rose 2.9% on 12 Feb.\n\n"
        "From the policies: Tier A can't rise in Ramadan."
    )
