"""Reine Matching-Logik der Filterregeln (_rule_hay / _first_match).

Diese Helfer werden von apply_rules (Posteingang) UND sweep_block_folders
(Spam/überwachte Ordner) geteilt. Hier ohne IMAP getestet: Fake-Nachrichten +
Fake-Regeln, nur die Trefferlogik.
"""
from types import SimpleNamespace

from app.mail.imap import _first_match, _rule_hay


def _msg(uid="1", from_="", name="", to=(), subject=""):
    return SimpleNamespace(
        uid=uid, from_=from_, subject=subject, to=to,
        from_values=SimpleNamespace(name=name),
    )


def _rule(field="from", value="", *, enabled=True, delete_msg=False):
    return SimpleNamespace(field=field, value=value, enabled=enabled, delete_msg=delete_msg)


def test_hay_from_covers_address_and_display_name():
    m = _msg(from_="werbung@bad.example", name="Super Angebote")
    hay = _rule_hay(m, "from")
    assert "werbung@bad.example" in hay
    assert "super angebote" in hay  # Anzeigename wird mitgeprüft (kleingeschrieben)


def test_from_domain_matches_only_domain_part():
    m = _msg(from_="a@nervig.example")
    assert _rule_hay(m, "from_domain") == "nervig.example"
    assert _first_match(m, [_rule("from_domain", "nervig.example")]) is not None
    # Domain-Regel darf NICHT auf den Namensteil vor dem @ matchen
    assert _first_match(m, [_rule("from_domain", "a@")]) is None


def test_first_match_returns_first_enabled_hit():
    m = _msg(from_="spam@bad.example")
    r_off = _rule("from", "spam@bad.example", enabled=False)
    r_on = _rule("from", "bad.example")
    # Deaktivierte Regel wird übersprungen, die aktive greift
    assert _first_match(m, [r_off, r_on]) is r_on


def test_comma_separated_terms_match_any():
    m = _msg(subject="Jetzt Bonus sichern")
    r = _rule("subject", "slot, casino, bonus")
    assert _first_match(m, [r]) is r


def test_no_match_and_empty_rule_value():
    m = _msg(from_="freund@gut.example")
    assert _first_match(m, [_rule("from", "spam@bad.example")]) is None
    assert _first_match(m, [_rule("from", "")]) is None  # leerer Wert matcht nie


def test_display_name_match_when_address_differs():
    # Klassischer Spam: wechselnde Adresse, gleicher Anzeigename
    m = _msg(from_="random123@throwaway.example", name="Gewinnbenachrichtigung")
    assert _first_match(m, [_rule("from", "Gewinnbenachrichtigung")]) is not None
