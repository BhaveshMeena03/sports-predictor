"""Fixture resolution for the Ask endpoint — the free step that decides
whether we spend an LLM call at all.

Real bug pinned: "first bundesliga game" resolved to a LA LIGA match, because
Bundesliga fixtures weren't inside the window yet and the resolver fell
through to earliest-overall. Once a league is named, an out-of-window league
must yield "unresolved", never another league's fixture.
"""

from app.services.ask import resolve_fixture

FIXTURES = [
    {"league": "la_liga", "league_label": "La Liga", "date": "2026-08-15",
     "home": "Alaves", "away": "Getafe"},
    {"league": "la_liga", "league_label": "La Liga", "date": "2026-08-16",
     "home": "Celta Vigo", "away": "Osasuna"},
    {"league": "premier_league", "league_label": "Premier League", "date": "2026-08-21",
     "home": "Arsenal", "away": "Coventry City"},
    {"league": "premier_league", "league_label": "Premier League", "date": "2026-08-22",
     "home": "Tottenham Hotspur", "away": "Everton"},
]


class TestResolution:
    def test_league_plus_first(self):
        r = resolve_fixture("first pl match what do you think", FIXTURES)
        assert r and (r["home"], r["away"]) == ("Arsenal", "Coventry City")

    def test_team_name(self):
        r = resolve_fixture("what happens when arsenal play", FIXTURES)
        assert r and r["home"] == "Arsenal"

    def test_nickname(self):
        r = resolve_fixture("can spurs win?", FIXTURES)
        assert r and r["home"] == "Tottenham Hotspur"

    def test_named_league_out_of_window_is_unresolved(self):
        """The bug: never answer a Bundesliga question with a La Liga match."""
        assert resolve_fixture("who wins the first bundesliga game", FIXTURES) is None

    def test_vague_question_is_unresolved(self):
        assert resolve_fixture("hello there", FIXTURES) is None

    def test_bare_first_match_takes_earliest(self):
        r = resolve_fixture("what happens in the first match", FIXTURES)
        assert r and r["date"] == "2026-08-15"

    def test_accents_do_not_block_matching(self):
        fx = [{"league": "la_liga", "league_label": "La Liga", "date": "2026-08-20",
               "home": "Atletico Madrid", "away": "Malaga"}]
        assert resolve_fixture("atlético game prediction", fx) is not None
