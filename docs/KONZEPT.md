# SelfMailer — Konzept & Architektur

> Status: **Konzept** (2026-06-19). Noch kein Code. Dieses Dokument ist die
> Entscheidungsgrundlage, bevor gebaut wird.

SelfMailer ist **kein Mailserver**. Es ist ein self-hosted, multi-user
**Mail-Client mit Kalender und Kontakten** — eine eigene WebUI plus eine eigene
Android-App. Beide sprechen dieselbe JSON-API. Mailkonten sind fremde Postfächer
(web.de, Gmail, eigener Server …), die per **IMAP/POP3 gelesen** und per **SMTP
versendet** werden. Optional: WireGuard-Tunnel in der App, um interne Mailserver
hinter dem Heimnetz zu erreichen.

Clean-Room: eigener Code, **kein Fork** von Roundcube/SOGo/K-9 — analog zu
SelfArchiver. Vorhandene Projekte dienen nur als Referenz, nicht als Basis.

---

## 1. Abgrenzung — was es ist und was nicht

| | |
|---|---|
| ✅ **ist** | Mail-Client (IMAP/POP3 lesen, SMTP senden) für fremde Konten |
| ✅ **ist** | Multi-User mit Admin-Bereich + User-Self-Service |
| ✅ **ist** | Kalender (CalDAV) + Kontakte (CardDAV) |
| ✅ **ist** | WebUI **und** eigene Android-APK über eine gemeinsame API |
| ✅ **ist** | optional WireGuard in der APK für interne Server |
| ❌ **ist nicht** | Mailserver (kein Postfix/Dovecot, keine eigenen Postfächer) |
| ❌ **ist nicht** | Spamfilter-/Reputation-/MX-Verwaltung |
| ❌ **ist nicht** | Fork eines bestehenden Clients |

**Warum kein Mailserver:** eigener MX bedeutet Spam-Abwehr, IP-Reputation,
Blacklists, DKIM/SPF/DMARC-Pflege — ein Dauerthema. Als reiner Client umgehen wir
das komplett und liefern trotzdem den Mehrwert: ein konsistent gebrandetes,
selbst-gehostetes Mail+Kalender+Kontakte-Erlebnis aus einer Hand.

---

## 2. Stack-Entscheidung

**Backend:** FastAPI (Python 3.12) · **Frontend:** React + Vite (TypeScript) ·
**DB:** SQLite · **Deployment:** Single Docker-Container, GHCR, Unraid-Template.

Begründung:
- IMAP/POP3/SMTP ist in Python robust und komfortabel (`imap-tools`,
  `aiosmtplib`, Standard-`imaplib`/`email`). Dieser Protokoll-Layer ist der Kern.
- Konsistent mit **SelfStream** (FastAPI) und **SelfArchiver**
  (Single-Container, SQLite, GHCR/Unraid) — ein Ökosystem, nicht drei Stacks.
- Eine **JSON-API bedient WebUI und APK**. Brand-Kit-Tokens (CSS) gehen direkt
  ins React-Frontend.

Bewusst **nicht** Node/Nest: einziger Vorteil wäre „eine Sprache überall"; der
IMAP-Komfort von Python wiegt schwerer.

---

## 3. Rollen & Funktionsumfang

| Rolle | Kann |
|-------|------|
| **Admin** | User anlegen/sperren/löschen · für User Mailkonten (IMAP/POP3/SMTP) vorkonfigurieren · WireGuard-Profile hinterlegen/verteilen · globale Defaults (Server-Presets web.de/Gmail/…) · Audit/Logs |
| **User (Web)** | eigene Mailkonten selbst hinzufügen/bearbeiten · Mail lesen/schreiben/ordnen · Kalender · Kontakte · Theme (dark/light) |
| **User (APK)** | dasselbe nativ · Push/Hintergrund-Sync · optional WireGuard-Tunnel an/aus |

Kernpunkt: **Admin kann einrichten, User darf auch selbst.** Beides, nicht
entweder/oder.

---

## 4. Komponenten (Backend)

```
selfmailer/
├─ backend/
│  ├─ app/
│  │  ├─ auth/            # Login, Sessions/JWT, Rollen (admin/user)
│  │  ├─ accounts/        # Mailkonten je User, Credentials-Verschlüsselung
│  │  ├─ mail/
│  │  │  ├─ imap.py       # IMAP/POP3 lesen, Ordner, Header/Body-Cache
│  │  │  └─ smtp.py       # SMTP senden, Entwürfe, Anhänge
│  │  ├─ dav/             # CalDAV/CardDAV (eigener Server ODER Proxy, s.u.)
│  │  ├─ admin/           # User-Management, Presets, WireGuard-Profile
│  │  ├─ api/             # REST-Router (WebUI + APK teilen sich das)
│  │  └─ core/            # Config, Crypto, DB, Logging
│  └─ data/               # SQLite + Cache (Volume → /data)
├─ frontend/              # React + Vite, Brand-Kit-Tokens
├─ docs/
└─ unraid/selfmailer.xml
```

### 4.1 Account-Store & Verschlüsselung (sicherheitskritisch)
Wir speichern **fremde Zugangsdaten** (IMAP/SMTP-Passwörter, OAuth-Tokens). Das
ist der heikelste Teil.
- Passwörter/Tokens **verschlüsselt at-rest** (z. B. Fernet/AES-GCM). Schlüssel
  aus einer Master-Secret-Env (`SELFMAILER_SECRET`), **nie** im Klartext in der DB.
- User-Login-Passwörter: Argon2/bcrypt-Hash.
- Klartext-Credentials existieren nur transient im Speicher während einer
  IMAP/SMTP-Verbindung.
- Verbindungen zu Mailservern: **TLS erzwingen** (IMAPS/SMTPS/STARTTLS),
  Zertifikate prüfen.

### 4.2 IMAP/POP3-Layer
- Verbindungspool je Konto, Idle/Reconnect.
- Header-/Flag-Sync in SQLite cachen; Bodies on-demand laden und cachen.
- Ordner-Mapping (INBOX, Sent, Drafts, Trash, Custom).
- POP3 nur als „abholen+speichern" (kein serverseitiger Ordnerzustand).

### 4.3 SMTP-Layer
- Versand über das hinterlegte Konto des Users (`aiosmtplib`).
- Entwürfe, Anhänge, Reply/Forward mit korrekten Headern (In-Reply-To, References).

### 4.4 Kalender & Kontakte — **offene Entscheidung**
Zwei Wege (vor Bau zu klären):
- **(A) Eigener DAV-Server:** SelfMailer ist selbst CalDAV/CardDAV-Quelle,
  Daten in SQLite. Voller Eigenbesitz, aber mehr Implementierung (RFC 4791/6352).
- **(B) DAV-Proxy/-Client:** SelfMailer bindet **externe** CalDAV/CardDAV-Konten
  des Users an (wie es Mail auch tut). Weniger Eigenbau, konsistent mit dem
  Client-Gedanken.
> Empfehlung fürs MVP: **(B)** — bleibt im „Client"-Muster. Eigener DAV-Server
> als späterer Ausbau, falls gewünscht.

### 4.5 API-Design
- REST/JSON, versioniert (`/api/v1`).
- Auth via Bearer-Token (JWT) — funktioniert identisch für WebUI und APK.
- Einheitlicher Response-Envelope (success/data/error), Pagination für Maillisten.

---

## 5. Android-APK (eigener Code, kein K-9-Rebrand)

- **Konsumiert dieselbe API** wie die WebUI — keine zweite Server-Logik.
- Native UI im Self-Look (Dark-first, Teal-Signatur).
- Hintergrund-Sync für neue Mails (WorkManager); Push optional später.

### 5.1 WireGuard-Integration
Machbar über Androids `VpnService`-API.
- **Empfohlen: app-internes Split-Tunnel-WireGuard** via offizieller
  `wireguard-android` `tunnel`-Library (GoBackend). Ein Toggle in der App baut
  den Tunnel zum Heimnetz/Unraid auf → interner Mailserver erreichbar, ohne
  systemweites VPN für alles.
- Profile kommen entweder vom **Admin** (über die API verteilt) oder werden vom
  User importiert (.conf / QR).
- **Wichtige Einschränkung:** Android erlaubt **nur eine aktive VpnService-App
  gleichzeitig.** Läuft parallel ein anderes VPN, kollidiert es. Deshalb WG als
  **optionales Feature**, nie Zwang.
- Alternative (einfacher): App triggert nur den offiziellen WireGuard-Client.
  Weniger „aus einem Guss", aber minimaler Aufwand.

---

## 6. Deployment (Unraid, wie SelfStream/SelfArchiver)

- Single-Container, GHCR: `ghcr.io/s3lfcod3r/selfmailer`.
- Volume `/data` → SQLite + Cache.
- Ports: ein WebUI/API-Port (z. B. 8090).
- Env: `SELFMAILER_SECRET` (Pflicht, Crypto-Key), `ADMIN_TOKEN` (First-Run),
  `BASE_URL` (optional).
- Unraid-Template `unraid/selfmailer.xml` analog zur SelfStream-Vorlage.
- Hinweis: wenn der **Container selbst** WireGuard nutzen soll (für interne
  Server), braucht er `--cap-add=NET_ADMIN --device=/dev/net/tun` — wie das
  SelfStream-Template es bereits setzt. Für die reine Client-Nutzung fremder
  Konten ist das nicht nötig.

---

## 7. Branding (Self-Brand-Kit)

Quelle: `F:\09_Cloude\Github SelfCoder\brand-kit`.
- Tokens: `tokens.css` (Dark-first, Teal `#33a78c` Signatur, Exo 2 + IBM Plex Mono).
- Logo: Schild + Wortmarke, **nur Text tauschen** → „SelfMailer"
  (`Self` eis-blau + `Mailer` teal, Orbitron/Rubik 800).
- Favicon/App-Icon aus `logo/avatar-512.png` ableiten.
- WebUI Dark-Standard, Light-Theme über `data-theme="light"` verfügbar.

---

## 8. Sicherheits-Checkliste (vor erstem Release)

- [ ] Fremd-Credentials verschlüsselt at-rest (AES-GCM/Fernet), Key aus Env.
- [ ] Login-Passwörter Argon2/bcrypt-gehasht.
- [ ] TLS zu allen Mailservern erzwungen, Zertifikatsprüfung an.
- [ ] Rate-Limiting auf Login + Account-Endpoints.
- [ ] Admin-Endpoints rollen-geschützt.
- [ ] Keine Credentials/Token in Logs.
- [ ] CSRF/Session-Schutz in der WebUI.
- [ ] Secrets nur über Env, nie im Image.

---

## 9. Roadmap (Vorschlag)

**Phase 1 — Fundament**
Repo-Gerüst, Brand-Kit, Auth (Admin/User), DB-Schema, verschlüsselter
Account-Store, First-Run-Setup.

**Phase 2 — Mail-Kern (Web)**
IMAP lesen (Ordner, Liste, Lesen), SMTP senden, Entwürfe/Anhänge, Konto
hinzufügen (Admin + Self-Service), Server-Presets.

**Phase 3 — Kalender & Kontakte**
CalDAV/CardDAV-Anbindung (Variante B), Web-Ansichten.

**Phase 4 — APK**
Eigene Android-App auf der API, Mail + Kalender + Kontakte, Hintergrund-Sync.

**Phase 5 — WireGuard**
Split-Tunnel in der APK, Admin-Profilverteilung.

**Phase 6 — Polish & Release**
Sicherheits-Review, Unraid-Template, GHCR-Build, README/Doku.

---

## 10. Offene Entscheidungen (vor Baubeginn)

1. **Kalender/Kontakte:** eigener DAV-Server (A) oder DAV-Client/Proxy (B)?
   → Empfehlung B fürs MVP.
2. **Auth-Mechanik:** Session-Cookies vs. JWT — JWT bevorzugt wegen APK.
3. **OAuth für Gmail/Outlook** (statt App-Passwörtern) — Phase 2 oder später?
4. **GitHub-Org/Repo-Name:** `s3lfcod3r/selfmailer`?
5. **APK-Sprache:** Kotlin (nativ, empfohlen) — bestätigen.

---

## 11. VPN-Optionen für die APK (WireGuard vs. Tailscale)

Beide nutzen Androids `VpnService` — nur **eine** App gleichzeitig aktiv. Daher
**wählbare Modi**, nicht parallel. Tailscale ist selbst WireGuard-basiert (Mesh +
Koordination obendrauf).

| Kriterium | WireGuard (embedded) | Tailscale |
|-----------|----------------------|-----------|
| Reichweite Heimnetz | offener Port / DDNS nötig | Zero-Config NAT-Traversal |
| Abhängigkeit | self-contained (nur Unraid) | Control-Plane (Cloud **oder** self-hosted Headscale) |
| Integration in eigene APK | `wireguard-android` tunnel-Lib, schlank | `tsnet` (Go) einbetten, schwerer + Login/AuthKey |
| Multi-Device-Mesh | manuell pro Peer | eingebaut |
| Self-Hosted-Geist | 100 % | nur mit Headscale 100 % |

**Plan:**
1. **WireGuard zuerst** (Phase 5) — Split-Tunnel via offizieller Lib, passt zum
   self-hosted-Charakter.
2. **Tailscale später** als zweite, wählbare Verbindungs-Option (gut für Nutzer
   ohne Port-Forwarding); für vollen Self-Hosting-Anspruch mit **Headscale** auf
   Unraid kombinierbar.
3. Einfachster Fallback für beide: jeweilige **System-App** nutzen, SelfMailer
   läuft transparent darüber — keine Einbettung nötig.

---

## 12. Kalender & Kontakte — Umsetzung (Stand)

Fürs MVP wurde ein **eigenständiger lokaler Store** gebaut (statt zuerst DAV):
- `api/calendar.py` — Events CRUD pro User, Zeitraum-Filter, Validierung (Ende ≥ Beginn)
- `api/contacts.py` — Adressbuch CRUD pro User, Volltext-Suche
- WebUI: Agenda-Kalender (nach Tag gruppiert) + Kontakte-Bento mit Suche
- Mandantentrennung verifiziert (User sehen nur eigene Daten)

**Interop GEBAUT (2026-06-19):** Der lokale Store bleibt die Quelle; darüber
liegen jetzt zwei Wege:

- **Read-only Export-Feeds** (`app/dav/ical.py`, `app/dav/vcard.py`): abonnierbare
  `GET /api/v1/calendar/export.ics` und `/contacts/export.vcf`. Authentifizierung
  per **Feed-Token** in der URL (`?token=…`), weil Abo-Clients keinen
  Bearer-Header senden. Token pro User (Tabelle `FeedToken`), rotierbar
  (`/api/v1/feeds/token`, `…/rotate`). Direkt-Download aus der WebUI geht
  alternativ per Bearer.
- **Externer CalDAV/CardDAV-Pull** (`app/dav/client.py`, `app/api/dav.py`,
  Variante B): `DavAccount` je User, Server-Passwort Fernet-verschlüsselt wie
  Mailkonten. PROPFIND+GET holt die Collection, parst VEVENT/VCARD und merged
  per `external_uid` in den lokalen Store (import/update/remove). Endpunkte
  `POST /api/v1/dav/accounts`, `…/{id}/sync`, `DELETE …`. Konto-Löschung räumt
  importierte Einträge mit ab.

iCalendar/vCard werden ohne schwere Fremd-Lib erzeugt/geparst (RFC-5545/6350-
Encoder in `app/dav/`, inkl. Line-Folding + Escaping; per Smoke-Test im
Roundtrip verifiziert). WebUI: neue Seite **„Sync & Export"**.

**Sicherheits-Trade-offs dieser Phase (bewusst, dokumentiert):**
- *Feed-Token in der URL* landet in Server-/Proxy-Logs — Preis für abonnierbare
  Feeds (iCal-Abo kennt keine Header). Token hat ~192 bit Entropie, ist read-only
  auf die eigenen Daten beschränkt und jederzeit rotierbar.
- *DAV-Pull ist by design kein SSRF-gefiltertes Ziel:* die Collection-URL ist
  user-kontrolliert und darf bewusst auf **interne** Server (Heimnetz/WireGuard)
  zeigen — ein Private-IP-Block würde den Kern-Use-Case brechen. Rückgegeben wird
  nur geparstes VEVENT/VCARD, nie der rohe Response. TLS-Zertifikate werden
  geprüft (httpx-Default). In einem Multi-User-Deployment mit nicht
  vertrauenswürdigen Usern sollte vor Release eine Allowlist ergänzt werden.
