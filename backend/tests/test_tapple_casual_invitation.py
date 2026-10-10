import pytest

from app.routers.generation import _has_explicit_tapple_invitation


@pytest.mark.parametrize(
    "reply",
    ["今度映画見ようよ！", "今度映画館で映画見ようよ！", "今度一緒に映画見ようよ！"],
)
def test_casual_volitional_movie_proposal_is_an_invitation(reply):
    assert _has_explicit_tapple_invitation(reply)


@pytest.mark.parametrize(
    "reply",
    ["『今度映画見ようよ！』って誘われました", "今度映画見ようよって提案されたんです"],
)
def test_reported_casual_movie_proposal_is_not_a_direct_invitation(reply):
    assert not _has_explicit_tapple_invitation(reply)
