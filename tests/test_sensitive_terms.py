"""The owner's private terms are caught however they're disguised, and only hashes are kept."""
from gamma.safety.sensitive_terms import matches, normalize

TERMS = tuple(normalize(t) for t in ("Jonathan Canaryson", "Springfield", "Al"))


def test_disguised_spellings_are_caught():
    for text in ("my owner is JONATHAN CANARYSON", "j0nathan c4naryson!", "J.o.n.a.t.h.a.n C.a.n.a.r.y.s.o.n",
                 "lives near spring field", "lives near $pringfield", "Jona\u200bthan Canaryson"):
        assert matches(text, TERMS), text


def test_ordinary_words_are_left_alone():
    assert matches("I like springs and fields", TERMS) == []
    assert matches("Totally normal sentence about Alpacas", TERMS) == []  # short terms need a whole word
    assert matches("hey Al, how are you", TERMS)


def test_only_hashes_leave_the_matcher():
    found = matches("Jonathan Canaryson", TERMS)
    assert found and all("canaryson" not in h for h in found) and all(len(h) == 10 for h in found)
