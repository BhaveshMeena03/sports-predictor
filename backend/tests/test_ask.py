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


class TestNicknameSafety:
    """Generic tokens must not hijack resolution."""

    FIXTURES = [
        {"league": "premier_league", "league_label": "Premier League",
         "date": "2026-08-21", "home": "Manchester City", "away": "Brentford"},
        {"league": "premier_league", "league_label": "Premier League",
         "date": "2026-08-22", "home": "Coventry City", "away": "Leeds United"},
        {"league": "premier_league", "league_label": "Premier League",
         "date": "2026-08-20", "home": "Manchester United", "away": "Fulham"},
        {"league": "premier_league", "league_label": "Premier League",
         "date": "2026-08-23", "home": "Newcastle United", "away": "Everton"},
    ]

    def test_coventry_city_does_not_become_man_city(self):
        r = resolve_fixture("can coventry city stay up this season?", self.FIXTURES)
        assert r and r["home"] == "Coventry City"

    def test_full_name_beats_shared_last_word(self):
        """'Newcastle United' must not lose the tie to Manchester United just
        because Man Utd's fixture is a day earlier."""
        r = resolve_fixture("newcastle united prediction please", self.FIXTURES)
        assert r and r["home"] == "Newcastle United"

    def test_man_city_nickname_still_works(self):
        r = resolve_fixture("man city this weekend?", self.FIXTURES)
        assert r and r["home"] == "Manchester City"

    def test_nickname_needs_word_boundary(self):
        """'inter' must not fire inside unrelated words."""
        fx = [{"league": "serie_a", "league_label": "Serie A",
               "date": "2026-08-23", "home": "Inter Milan", "away": "Como"}]
        assert resolve_fixture("my printer is broken", fx) is None


class TestMultiBuilder:
    """Parlay requests. The honest headline is the COMBINED probability —
    a '2.5x, not very risky' multi is roughly a 35-40% shot, and the whole
    point of this feature is showing that rather than hiding it."""

    FX = [{"league": "premier_league", "league_label": "Premier League",
           "date": f"2026-08-2{i}", "home": f"Home{i}", "away": f"Away{i}"}
          for i in range(6)]

    def preds(self, probs):
        return {("premier_league", f"Home{i}", f"Away{i}"): {"probs": p}
                for i, p in enumerate(probs)}

    def test_detects_multi_requests(self):
        from app.services.ask import wants_multi
        assert wants_multi("help me built a 2.5x multi") == 2.5
        assert wants_multi("build me a parlay") == 2.5      # sensible default
        assert wants_multi("4x acca please") == 4.0
        assert wants_multi("give me an accumulator @3.0") == 3.0

    def test_ignores_non_multi_questions(self):
        from app.services.ask import wants_multi
        assert wants_multi("what happens when arsenal play") is None
        assert wants_multi("first pl match prediction") is None

    def test_rejects_absurd_targets(self):
        """A 500x 'multi' is a lottery ticket, not a request we honour
        literally — fall back to the sane default."""
        from app.services.ask import wants_multi
        assert wants_multi("build me a 500x multi") == 2.5

    def test_combined_probability_is_the_product_of_legs(self):
        from app.services.ask import build_multi
        m = build_multi(self.FX, self.preds([[0.75, .15, .10]] * 6), 2.5)
        prod = 1.0
        for leg in m["legs"]:
            prod *= leg["probability"]
        assert abs(prod - m["combined_probability"]) < 1e-3

    def test_skips_coinflip_legs(self):
        """Padding a parlay with sub-40% picks is how 'low risk' quietly
        becomes a 15% shot."""
        from app.services.ask import build_multi
        m = build_multi(self.FX, self.preds([[0.34, .33, .33]] * 6), 2.5)
        assert m is None

    def test_picks_most_confident_first(self):
        from app.services.ask import build_multi
        probs = [[0.45, .3, .25], [0.80, .1, .1], [0.60, .2, .2],
                 [0.50, .3, .2], [0.44, .3, .26], [0.42, .3, .28]]
        m = build_multi(self.FX, self.preds(probs), 2.5)
        ordered = [leg["probability"] for leg in m["legs"]]
        assert ordered == sorted(ordered, reverse=True)

    def test_fair_odds_are_the_reciprocal_of_probability(self):
        from app.services.ask import build_multi
        m = build_multi(self.FX, self.preds([[0.75, .15, .10]] * 6), 2.5)
        for leg in m["legs"]:
            assert abs(leg["fair_odds"] - 1 / leg["probability"]) < 0.01

    def test_caps_leg_count(self):
        """A 50x 'multi' must not become a 20-leg fantasy."""
        from app.services.ask import build_multi
        m = build_multi(self.FX, self.preds([[0.55, .25, .20]] * 6), 50)
        assert len(m["legs"]) <= 6
        assert m["reached_target"] is False    # and it says so honestly
