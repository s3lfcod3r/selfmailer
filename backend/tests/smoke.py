import os, tempfile
os.environ["SELFMAILER_SECRET"] = "test-secret-this-is-long-enough-1234567890"
os.environ["SELFMAILER_DB_PATH"] = os.path.join(tempfile.gettempdir(), "selfmailer_smoke.db")
# frische DB
try:
    os.remove(os.environ["SELFMAILER_DB_PATH"])
except FileNotFoundError:
    pass

from fastapi.testclient import TestClient
from app.main import app
from app.core.db import init_db as _init_db
_init_db()

c = TestClient(app)

# health
r = c.get("/api/health"); assert r.status_code == 200, r.text
print("health:", r.json())

# setup status -> needs_setup True
r = c.get("/api/v1/auth/status"); assert r.json()["needs_setup"] is True, r.text
print("status:", r.json())

# setup admin
r = c.post("/api/v1/auth/setup", json={"username":"admin@self","password":"supersecret","display_name":"Admin"})
assert r.status_code == 201, r.text
tok = r.json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
print("setup OK, token len:", len(tok))

# me
r = c.get("/api/v1/auth/me", headers=H); assert r.status_code==200, r.text
print("me:", r.json())

# second setup must conflict
r = c.post("/api/v1/auth/setup", json={"username":"x@y","password":"supersecret"})
assert r.status_code == 409, r.text
print("second setup blocked:", r.status_code)

# login wrong
r = c.post("/api/v1/auth/login", json={"username":"admin@self","password":"wrong"})
assert r.status_code == 401, r.text
print("wrong login blocked:", r.status_code)

# notes CRUD
r = c.post("/api/v1/notes", json={"title":"Erste Notiz","body":"Hallo Welt"}, headers=H)
assert r.status_code==201, r.text
nid = r.json()["id"]
r = c.patch(f"/api/v1/notes/{nid}", json={"pinned": True}, headers=H); assert r.json()["pinned"] is True, r.text
r = c.get("/api/v1/notes", headers=H); assert len(r.json())==1, r.text
print("notes:", r.json()[0]["title"], "pinned=", r.json()[0]["pinned"])
r = c.delete(f"/api/v1/notes/{nid}", headers=H); assert r.status_code==204, r.text

# admin create user
r = c.post("/api/v1/admin/users", json={"username":"sven@self","password":"userpass12","role":"user"}, headers=H)
assert r.status_code==201, r.text
print("admin created user:", r.json()["username"])

# new user login + account add (encrypted) without real connection test
r = c.post("/api/v1/auth/login", json={"username":"sven@self","password":"userpass12"})
uH = {"Authorization": f"Bearer {r.json()['access_token']}"}
r = c.post("/api/v1/accounts", json={
    "label":"Web.de","email":"sven@web.de","password":"app-pw-secret",
    "imap_host":"imap.web.de","imap_port":993,"smtp_host":"smtp.web.de","smtp_port":587
}, headers=uH)
assert r.status_code==201, r.text
print("account added:", r.json())
# ensure secret not leaked in response
assert "secret" not in r.text.lower() or "secret_enc" not in r.json(), r.text

# verify stored secret is encrypted in DB and decrypts back
from sqlmodel import Session, select
from app.core.db import engine
from app.models import MailAccount
from app.core.crypto import decrypt
with Session(engine) as s:
    acc = s.exec(select(MailAccount)).first()
    assert acc.secret_enc != "app-pw-secret", "Passwort liegt im Klartext!"
    assert decrypt(acc.secret_enc) == "app-pw-secret", "Entschluesselung fehlgeschlagen"
print("at-rest encryption OK (stored != plaintext, decrypts correctly)")

# user must NOT reach admin endpoints
r = c.get("/api/v1/admin/users", headers=uH)
assert r.status_code==403, r.text
print("RBAC OK: user blocked from admin (403)")

# Mehr-User: zweiten User anlegen, Liste pruefen
r = c.post("/api/v1/admin/users", json={"username":"lisa@self","password":"lisapass12","role":"user"}, headers=H)
assert r.status_code==201, r.text
r = c.get("/api/v1/admin/users", headers=H)
assert len(r.json()) == 3, r.text  # admin + sven + lisa
print("multi-user OK: total users =", len(r.json()))

# Selbstschutz: Admin (id=1) darf sich nicht loeschen / deaktivieren
r = c.delete("/api/v1/admin/users/1", headers=H)
assert r.status_code==400, r.text
r = c.patch("/api/v1/admin/users/1/active?active=false", headers=H)
assert r.status_code==400, r.text
print("self-protect OK: admin cannot delete/deactivate self (400)")

# Admin kann User-Passwort zuruecksetzen, danach Login mit neuem PW
sven_id = next(u["id"] for u in c.get("/api/v1/admin/users", headers=H).json() if u["username"]=="sven@self")
r = c.patch(f"/api/v1/admin/users/{sven_id}/password", json={"new_password":"neuespass99"}, headers=H)
assert r.status_code==200, r.text
r = c.post("/api/v1/auth/login", json={"username":"sven@self","password":"neuespass99"})
assert r.status_code==200, r.text
print("admin password-reset OK")

# Admin konfiguriert Mailkonto FUER einen User vor
r = c.post(f"/api/v1/admin/users/{sven_id}/accounts", json={
    "label":"Arbeit","email":"sven@firma.de","password":"firmenpw123",
    "imap_host":"imap.firma.de","smtp_host":"smtp.firma.de"
}, headers=H)
assert r.status_code==201, r.text
admin_acc_id = r.json()["id"]
r = c.get(f"/api/v1/admin/users/{sven_id}/accounts", headers=H)
assert any(a["email"]=="sven@firma.de" for a in r.json()), r.text
print("admin-preconfigured account OK:", len(r.json()), "Konten fuer User")

# Sven sieht das vom Admin angelegte Konto in seiner eigenen Liste
r = c.post("/api/v1/auth/login", json={"username":"sven@self","password":"neuespass99"})
svenH = {"Authorization": f"Bearer {r.json()['access_token']}"}
r = c.get("/api/v1/accounts", headers=svenH)
assert any(a["email"]=="sven@firma.de" for a in r.json()), r.text
print("user sees admin-configured account OK")

# Admin loescht das Konto wieder
r = c.delete(f"/api/v1/admin/users/{sven_id}/accounts/{admin_acc_id}", headers=H)
assert r.status_code==204, r.text
print("admin delete user-account OK")

# Kalender: Event anlegen, listen, Range-Filter, loeschen
ev = {"title":"Zahnarzt","location":"Praxis","start":"2026-07-01T09:00:00","end":"2026-07-01T09:30:00"}
r = c.post("/api/v1/calendar/events", json=ev, headers=svenH); assert r.status_code==201, r.text
eid = r.json()["id"]
# Ende vor Beginn -> 400
bad = {"title":"x","start":"2026-07-01T10:00:00","end":"2026-07-01T09:00:00"}
r = c.post("/api/v1/calendar/events", json=bad, headers=svenH); assert r.status_code==400, r.text
r = c.get("/api/v1/calendar/events?start_from=2026-06-01T00:00:00&start_to=2026-08-01T00:00:00", headers=svenH)
assert any(e["title"]=="Zahnarzt" for e in r.json()), r.text
print("calendar OK:", len(r.json()), "Event(s) im Zeitraum")
r = c.delete(f"/api/v1/calendar/events/{eid}", headers=svenH); assert r.status_code==204, r.text

# Kontakte: anlegen, Suche, aktualisieren, loeschen
r = c.post("/api/v1/contacts", json={"first_name":"Lisa","last_name":"Meier","email":"lisa@example.de","organization":"ACME"}, headers=svenH)
assert r.status_code==201, r.text
cid = r.json()["id"]
r = c.get("/api/v1/contacts?q=meier", headers=svenH); assert len(r.json())==1, r.text
r = c.patch(f"/api/v1/contacts/{cid}", json={"phone":"0123-456"}, headers=svenH); assert r.json()["phone"]=="0123-456", r.text
print("contacts OK: search + update")
# Mandantentrennung: Admin (anderer User) sieht Svens Kontakt nicht
r = c.get("/api/v1/contacts?q=meier", headers=H); assert len(r.json())==0, r.text
print("contacts isolation OK (admin sieht User-Kontakt nicht)")
r = c.delete(f"/api/v1/contacts/{cid}", headers=svenH); assert r.status_code==204, r.text

# ============================================================
# DAV / Export-Phase
# ============================================================
import datetime as _dt
from types import SimpleNamespace as NS
from app.dav.ical import build_calendar, parse_events
from app.dav.vcard import build_vcards, parse_vcards
import app.api.dav as dav_api

def _utc(y, mo, d, h, mi):
    return _dt.datetime(y, mo, d, h, mi, tzinfo=_dt.timezone.utc)

# ---- Format-Roundtrip iCalendar (Escaping, Multibyte, Line-Folding) ----
ev_rt = NS(id=1, external_uid="", title="Café, „Test“", description="Zeile1\nZeile2",
           location="Raum;3", start=_utc(2026,9,1,8,0), end=_utc(2026,9,1,9,30), all_day=False)
ics_rt = build_calendar([ev_rt])
p = parse_events(ics_rt)[0]
assert p["title"] == "Café, „Test“", p["title"]
assert p["description"] == "Zeile1\nZeile2", repr(p["description"])
assert p["location"] == "Raum;3", p["location"]
assert p["start"] == ev_rt.start and p["end"] == ev_rt.end, (p["start"], p["end"])
long_title = "X" * 200  # erzwingt Line-Folding (>75 Oktette)
ev_long = NS(id=2, external_uid="", title=long_title, description="", location="",
             start=_utc(2026,9,2,8,0), end=_utc(2026,9,2,9,0), all_day=False)
assert parse_events(build_calendar([ev_long]))[0]["title"] == long_title, "Folding-Roundtrip fehlgeschlagen"
# Ganztags: DTEND exklusiv -> inklusiver letzter Tag muss erhalten bleiben
ev_ad = NS(id=3, external_uid="", title="Urlaub", description="", location="",
           start=_utc(2026,9,5,0,0), end=_utc(2026,9,7,0,0), all_day=True)
p_ad = parse_events(build_calendar([ev_ad]))[0]
assert p_ad["all_day"] is True and p_ad["end"] == ev_ad.end, (p_ad["all_day"], p_ad["end"])
print("ical roundtrip OK (escape, multibyte, folding, all-day)")

# ---- Format-Roundtrip vCard ----
ct_rt = NS(id=1, external_uid="", first_name="Anna", last_name="Müller; Test",
           email="a@b.de", phone="123", organization="ACME, Inc", notes="hi\nda")
pc = parse_vcards(build_vcards([ct_rt]))[0]
assert pc["last_name"] == "Müller; Test" and pc["first_name"] == "Anna", pc
assert pc["organization"] == "ACME, Inc" and pc["notes"] == "hi\nda", pc
print("vcard roundtrip OK (escape, structured N/ORG)")

# ---- Export-Feeds: Token + Auth-Varianten ----
r = c.get("/api/v1/feeds/token", headers=svenH); assert r.status_code==200, r.text
ftok = r.json()["token"]; assert ftok and r.json()["calendar_url"].endswith(ftok)
# lokale Daten fuer den Export anlegen
c.post("/api/v1/calendar/events", json={"title":"Export-Test, x; y","location":"Büro",
       "start":"2026-09-01T08:00:00","end":"2026-09-01T09:00:00"}, headers=svenH)
c.post("/api/v1/contacts", json={"first_name":"Max","last_name":"Mustermann",
       "email":"max@example.de","phone":"+49 30 1234"}, headers=svenH)
# Export via Token (ohne Bearer) — so wuerde ein Handy-Kalender abonnieren
r = c.get(f"/api/v1/calendar/export.ics?token={ftok}")
assert r.status_code==200 and "text/calendar" in r.headers["content-type"], r.text
assert "BEGIN:VCALENDAR" in r.text and "Export-Test\\, x\\; y" in r.text, r.text
# Export via Bearer (Direkt-Download aus der WebUI)
r = c.get("/api/v1/contacts/export.vcf", headers=svenH)
assert r.status_code==200 and "BEGIN:VCARD" in r.text and "FN:Max Mustermann" in r.text, r.text
# Falscher Token -> 401
assert c.get("/api/v1/calendar/export.ics?token=falsch").status_code==401
# Ohne jede Auth -> 401
assert c.get("/api/v1/calendar/export.ics").status_code==401
# Rotation macht alten Token ungueltig
r = c.post("/api/v1/feeds/token/rotate", headers=svenH); newtok = r.json()["token"]
assert newtok != ftok
assert c.get(f"/api/v1/calendar/export.ics?token={ftok}").status_code==401
assert c.get(f"/api/v1/calendar/export.ics?token={newtok}").status_code==200
print("export feeds OK (token+bearer auth, escaping, rotation invalidates)")

# ---- Externer CalDAV-Pull (gemockter fetch_collection) ----
ext_a = NS(id=None, external_uid="ext-1@srv", title="Extern A", description="d", location="",
           start=_utc(2026,10,1,10,0), end=_utc(2026,10,1,11,0), all_day=False)
ext_b = NS(id=None, external_uid="ext-2@srv", title="Extern B", description="", location="Halle",
           start=_utc(2026,10,2,10,0), end=_utc(2026,10,2,11,0), all_day=False)
dav_api.client.fetch_collection = lambda url, usr, pw: [("/cal/a.ics", build_calendar([ext_a, ext_b]))]
r = c.post("/api/v1/dav/accounts", json={"kind":"caldav","label":"Nextcloud",
       "url":"https://nc.example/dav/cal/","username":"sven","password":"dav-pw"}, headers=svenH)
assert r.status_code==201, r.text
dav_cal_id = r.json()["id"]
assert "password" not in r.text.lower() and "secret_enc" not in r.json(), r.text
# verschluesselt gespeichert?
from app.models import DavAccount as _DavAcc
with Session(engine) as s:
    dacc = s.get(_DavAcc, dav_cal_id)
    assert dacc.secret_enc != "dav-pw" and decrypt(dacc.secret_enc) == "dav-pw", "DAV-Secret nicht verschluesselt"
# Erst-Sync: 2 importiert
r = c.post(f"/api/v1/dav/accounts/{dav_cal_id}/sync", headers=svenH).json()
assert r["ok"] is True and r["imported"]==2 and r["updated"]==0, r
# importierte Events erscheinen lokal + im Export
r = c.get(f"/api/v1/calendar/export.ics?token={newtok}")
assert "Extern A" in r.text and "Extern B" in r.text, "Import nicht im Export sichtbar"
# Re-Sync mit geaenderter Quelle: A geaendert, B weg, C neu -> 1 updated, 1 imported, 1 removed
ext_a2 = NS(id=None, external_uid="ext-1@srv", title="Extern A neu", description="d", location="",
            start=_utc(2026,10,1,10,0), end=_utc(2026,10,1,12,0), all_day=False)
ext_c = NS(id=None, external_uid="ext-3@srv", title="Extern C", description="", location="",
           start=_utc(2026,10,3,10,0), end=_utc(2026,10,3,11,0), all_day=False)
dav_api.client.fetch_collection = lambda url, usr, pw: [("/cal/x.ics", build_calendar([ext_a2, ext_c]))]
r = c.post(f"/api/v1/dav/accounts/{dav_cal_id}/sync", headers=svenH).json()
assert r["updated"]==1 and r["imported"]==1 and r["removed"]==1, r
# Mandantentrennung: Admin sieht Svens importierte Events nicht
r = c.get("/api/v1/calendar/events?start_from=2026-01-01T00:00:00&start_to=2027-01-01T00:00:00", headers=H)
assert not any(e["title"].startswith("Extern") for e in r.json()), "DAV-Import nicht mandantengetrennt"
print("caldav pull OK (import/update/remove merge, isolation)")

# ---- Google Zwei-Wege-Push (Google-API gemockt) ----
import app.dav.google as _g
_g.access_token = lambda cid, cs, rt: "tok-x"
_g.primary_calendar_id = lambda tok: "primary@cal"
_pushed = {}
_g.create_event = lambda tok, cal, ev: (_pushed.update(create=(cal, ev)) or "EVT123")
_g.patch_event = lambda tok, cal, eid, ev: _pushed.update(patch=(cal, eid))
_g.delete_event = lambda tok, cal, eid: _pushed.update(delete=(cal, eid))
# gcal-Konto anlegen (access_token-Test ist gemockt)
r = c.post("/api/v1/dav/google", json={"email":"sven@gmail.com","client_id":"cid",
       "client_secret":"cs","refresh_token":"rt","label":"Google"}, headers=svenH)
assert r.status_code==201, r.text
gacc_id = r.json()["id"]
# Anlegen mit Google-Ziel -> Push create + external_uid {cal}::{eventId}
r = c.post("/api/v1/calendar/events", json={"title":"GMeet","start":"2026-11-01T09:00:00Z",
       "end":"2026-11-01T10:00:00Z","dav_account_id":gacc_id,"gcal_calendar_id":"primary@cal"}, headers=svenH)
assert r.status_code==201, r.text
gev = r.json()
assert _pushed["create"][0]=="primary@cal", _pushed
assert gev["dav_account_id"]==gacc_id, gev
gev_id = gev["id"]
# Aendern -> Push patch auf die richtige Event-ID
r = c.patch(f"/api/v1/calendar/events/{gev_id}", json={"title":"GMeet v2"}, headers=svenH)
assert r.status_code==200 and _pushed["patch"]==("primary@cal","EVT123"), (r.text, _pushed)
# Loeschen -> Push delete (sonst kaeme der Termin beim naechsten Pull zurueck)
r = c.delete(f"/api/v1/calendar/events/{gev_id}", headers=svenH)
assert r.status_code==204 and _pushed["delete"]==("primary@cal","EVT123"), _pushed
# Rein lokaler Termin (kein Ziel) loest KEINEN Google-Call aus
_pushed.clear()
r = c.post("/api/v1/calendar/events", json={"title":"Lokal","start":"2026-11-02T09:00:00Z",
       "end":"2026-11-02T10:00:00Z"}, headers=svenH)
assert r.status_code==201 and "create" not in _pushed, _pushed
print("gcal two-way push OK (create/patch/delete mocked, local stays local)")

# ---- Externer CardDAV-Pull ----
ext_ct = NS(id=None, external_uid="c-1@srv", first_name="Ext", last_name="Kontakt",
            email="e@x.de", phone="", organization="ExtOrg", notes="")
dav_api.client.fetch_collection = lambda url, usr, pw: [("/card/a.vcf", build_vcards([ext_ct]))]
r = c.post("/api/v1/dav/accounts", json={"kind":"carddav","label":"NC Kontakte",
       "url":"https://nc.example/dav/card/","username":"sven","password":"pw2"}, headers=svenH)
dav_card_id = r.json()["id"]
r = c.post(f"/api/v1/dav/accounts/{dav_card_id}/sync", headers=svenH).json()
assert r["ok"] is True and r["imported"]==1, r
r = c.get("/api/v1/contacts?q=Kontakt", headers=svenH)
assert any(x["last_name"]=="Kontakt" and x["organization"]=="ExtOrg" for x in r.json()), r.text
print("carddav pull OK")

# ---- DAV-Sync-Fehler wird sauber gemeldet (kein Crash) ----
import httpx as _httpx
def _boom(url, usr, pw):
    raise _httpx.ConnectError("Verbindung fehlgeschlagen")
dav_api.client.fetch_collection = _boom
r = c.post(f"/api/v1/dav/accounts/{dav_cal_id}/sync", headers=svenH).json()
# Sicherer fester Fehlertext (kein Exception-Leak) — Hauptsache ok=False + Meldung.
assert r["ok"] is False and "Verbindungsfehler" in r["error"], r
print("dav error handling OK (ok=false, message surfaced)")

# ---- DAV-Konto loeschen raeumt importierte Eintraege ----
r = c.delete(f"/api/v1/dav/accounts/{dav_cal_id}", headers=svenH); assert r.status_code==204, r.text
r = c.get("/api/v1/calendar/events?start_from=2026-01-01T00:00:00&start_to=2027-01-01T00:00:00", headers=svenH)
assert not any(e["title"].startswith("Extern") for e in r.json()), "Importierte Events nach Konto-Loeschung uebrig"
r = c.delete(f"/api/v1/dav/accounts/{dav_card_id}", headers=svenH); assert r.status_code==204, r.text
print("dav account delete cleans imported entries OK")

print("\nALLE SMOKE-TESTS BESTANDEN")
