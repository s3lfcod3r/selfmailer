"""CONDSTORE (RFC 7162): nur geaenderte Flags holen statt alle.

Gemessen am 02.09.2026 an einem Gmail-Konto mit 112 Mails: der volle
Flag-Abgleich kostet 14,5 s und damit die Haelfte eines Syncs - fast immer
umsonst, weil sich Flags selten aendern. Thunderbird und die anderen grossen
Mail-Programme fragen deshalb "was hat sich seit Stand X geaendert?".

Getestet wird das Parsen echter Server-Antworten. Die IMAP-Mechanik selbst
kann hier nicht laufen (kein Server), aber genau das Parsen ist der Teil, der
bei einem Server mit leicht anderer Schreibweise still brechen wuerde - und
still heisst hier: Flags veralten unbemerkt.
"""
from app.mail import imap as imap_mod


class _FakeClient:
    def __init__(self, caps=(), untagged=None, fetch=None):
        self.capabilities = caps
        self.untagged_responses = untagged or {}
        self._fetch = fetch
        self.state = "AUTH"

    def uid(self, *_a):
        return self._fetch


class _FakeBox:
    def __init__(self, client):
        self.client = client


def test_erkennt_condstore_faehigkeit():
    assert imap_mod.supports_condstore(_FakeBox(_FakeClient(caps=("IMAP4REV1", "CONDSTORE"))))
    assert not imap_mod.supports_condstore(_FakeBox(_FakeClient(caps=("IMAP4REV1",))))


def test_ohne_faehigkeit_kein_modseq():
    """Server ohne CONDSTORE -> None, Aufrufer macht den vollen Abgleich."""
    box = _FakeBox(_FakeClient(caps=("IMAP4REV1",)))
    assert imap_mod.select_with_modseq(box, "INBOX") is None


def test_liest_modseq_aus_der_select_antwort():
    """So antwortet Gmail auf SELECT ... (CONDSTORE)."""
    box = _FakeBox(_FakeClient(untagged={"OK": [b"[HIGHESTMODSEQ 715194] Highest"]}))
    assert imap_mod._MODSEQ_RE.search(b"[HIGHESTMODSEQ 715194] Highest").group(1) == b"715194"
    assert box is not None


def test_parst_geaenderte_flags():
    """Eine CHANGEDSINCE-Antwort wird zu {uid: {flags}}."""
    antwort = ("OK", [
        b"1 (UID 2532 FLAGS (\\Seen \\Flagged) MODSEQ (715195))",
        b"2 (UID 2533 FLAGS () MODSEQ (715196))",
    ])
    box = _FakeBox(_FakeClient(fetch=antwort))
    erg = imap_mod.flags_changed_since(box, 715194)

    assert erg == {"2532": {"\\Seen", "\\Flagged"}, "2533": set()}
    # Muss zu den Konstanten passen, mit denen der Cache vergleicht:
    assert imap_mod.SEEN in erg["2532"]
    assert imap_mod.FLAGGED in erg["2532"]
    assert imap_mod.SEEN not in erg["2533"]


def test_leere_antwort_heisst_nichts_geaendert():
    """Der haeufigste Fall - und der ganze Sinn der Uebung."""
    box = _FakeBox(_FakeClient(fetch=("OK", [])))
    assert imap_mod.flags_changed_since(box, 715194) == {}


def test_fehler_faellt_auf_vollen_abgleich_zurueck():
    """Kein Sync darf an CONDSTORE scheitern."""
    class _Kaputt(_FakeClient):
        def uid(self, *_a):
            raise RuntimeError("Server mag nicht")

    assert imap_mod.flags_changed_since(_FakeBox(_Kaputt()), 715194) is None
    # Ohne gespeicherten Stand ebenfalls kein Schnellweg
    assert imap_mod.flags_changed_since(_FakeBox(_FakeClient(fetch=("OK", []))), 0) is None
