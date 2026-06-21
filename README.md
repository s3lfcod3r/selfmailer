<div align="center">

<img src="frontend/public/icon.png" width="120" alt="SelfMailer logo" />

# SelfMailer

**Self-hosted, multi-user e-mail client with calendar, contacts, notes & a native Android app — your own alternative to Synology MailPlus.**

[![Build](https://github.com/kabelsalatundklartext/selfmailer/actions/workflows/docker.yml/badge.svg)](https://github.com/kabelsalatundklartext/selfmailer/actions/workflows/docker.yml)
![Version](https://img.shields.io/badge/version-1.8.0-33A78C)
![License](https://img.shields.io/badge/license-private-8A9CAA)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Web](https://img.shields.io/badge/web-React%20%2B%20Vite-43D3AD)
![App](https://img.shields.io/badge/android-Kotlin%20%2B%20Compose-9DBDD0)

[English](#english) · [Deutsch](#deutsch)

</div>

---

<a id="english"></a>

## 🇬🇧 English

A single Docker container that is **not a mail server** — it's a **client** for the mail accounts you already have. It connects to any **IMAP / POP3** mailbox to read and to **SMTP** to send, and bundles a **calendar, contacts, notes and tasks** on top. One web UI **and** a native Android app talk to **one JSON API**.

Part of the **Self** family (SelfAuthenticator, SelfArchiver, SelfDashboard …) — same design system, same deploy style (GHCR → Unraid).

### ✨ Features

**📬 Mail**
- Multiple **IMAP / POP3 / SMTP** accounts per user
- **Thunderbird-style** stacked accounts with a per-account folder tree and **unread counters**
- Read **HTML mail** (sandboxed), download/open **attachments**, **SPF/DKIM/DMARC** spoofing check
- **Compose / reply / forward**, per-account **signatures**, drafts, read/delivery receipts
- **Flag (star)**, mark read/unread, delete, **move**, **cross-account transfer**, **bulk actions**
- **Filter rules** (move/star/mark on arrival), **server-side mailbox migration**
- **Search**, pagination, snippets, and a local **SQLite cache** kept warm by a background sync → the UI never waits on a slow provider

**📅 Organizer**
- **Calendar** — local events, month/agenda views, birthdays from contacts
- **Contacts** — rich address book (phones, address, organisation, website, birthday)
- **Notes** & **Tasks**
- **CalDAV / CardDAV** pull from external servers (Nextcloud, Synology …) + subscribable **ICS / vCard** export feeds

**🔔 Notifications (self-hosted, no Google)**
- Push on new mail via **ntfy** — the server pushes, your phone gets it, no Firebase/FCM
- **Per account, per folder** — choose exactly which mailboxes notify you
- Configurable in the **web UI and the app** (one shared setting)

**🔐 Security & platform**
- **Multi-user** with admin/user roles; admin can pre-configure accounts for users
- **2FA / TOTP** login with backup codes
- Account credentials **Fernet-encrypted at rest**
- **Native Android app** (Kotlin + Jetpack Compose) with biometric app-lock
- **i18n DE/EN**, dark/light themes + custom accent colours
- **Single container** — FastAPI + SQLite, no external database

### 🧭 Architecture

```
        ┌──────────────┐          ┌───────────────┐
        │   Web UI     │          │  Android app  │
        │ React + Vite │          │ Kotlin/Compose│
        └──────┬───────┘          └──────┬────────┘
               └────────  JSON API  ──────┘
                      /api/v1 · JWT (Bearer)
                          │
                 ┌────────▼─────────┐
                 │   FastAPI core   │  SQLite (cache + config)
                 │  + background    │  Fernet-encrypted secrets
                 │      sync        │
                 └────────┬─────────┘
        ┌─────────────────┼────────────────┬─────────────────┐
     IMAP / POP3        SMTP        CalDAV / CardDAV         ntfy
   (read mailboxes)  (send mail)  (sync cal & contacts)  (push on new mail)
```

### 🔔 Notifications setup (ntfy)

Self-hosted push without Google. The SelfMailer server detects new mail and posts to **your** ntfy server; the ntfy app on your phone shows it.

1. **Run an ntfy container** on Unraid (port e.g. `8095`, `NTFY_BASE_URL=http://<host>:8095`).
2. **Install the ntfy app** on your phone, add your server, and subscribe to the topic SelfMailer shows you.
3. In SelfMailer (**web** *user menu → 🔔 Notifications* **or app** *Settings → ntfy push*): enter the ntfy URL → **Save & enable** → pick the folders per account that should notify you.
4. *(Optional)* set `SELFMAILER_SYNC_INTERVAL=120` for ~2-minute push latency.

> The app also offers a **foreground polling** mode (~1 min, no extra infrastructure) as an alternative — it keeps a small persistent notification, which ntfy avoids.

### 🚀 Quick start (Docker)

```bash
cp .env.example .env
# generate a secret and put it in .env (SELFMAILER_SECRET):
python -c "import secrets; print(secrets.token_hex(32))"

docker compose up -d
```

Open `http://<host>:8090` → the **first-run setup** creates the admin account → then add your mail accounts.

### 📦 Unraid

Add the template from
`https://raw.githubusercontent.com/kabelsalatundklartext/selfmailer/main/unraid/selfmailer.xml`
or import it under *Docker → Add Container → Template*. Set the **Master Secret**, leave the rest on defaults.

> **Permissions:** the container runs **non-root** (uid `99` / gid `100` = `nobody:users`). If the appdata folder is owned by root, fix it once:
> `chown -R 99:100 /mnt/user/appdata/selfmailer` (or use *Tools → Docker Safe New Permissions*).

### ⚙️ Configuration (ENV, prefix `SELFMAILER_`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SELFMAILER_SECRET` | ✅ | – | JWT signing **and** at-rest encryption of mailbox passwords (≥ 32 chars). Changing it makes stored passwords unreadable. |
| `SELFMAILER_DB_PATH` | – | `/data/selfmailer.db` | SQLite path |
| `SELFMAILER_ADMIN_TOKEN` | – | – | If set, first-run admin setup requires this token |
| `SELFMAILER_BASE_URL` | – | – | Public base URL (e.g. for feed links) |
| `SELFMAILER_SYNC_INTERVAL` | – | `300` | Seconds between background syncs (lower = faster push) |
| `SELFMAILER_SYNC_DISABLE` | – | `0` | Turn off the background sync |
| `SELFMAILER_IMAP_TIMEOUT` | – | `15` | IMAP socket timeout (seconds) |
| `SELFMAILER_DAV_BLOCK_PRIVATE` | – | `false` | Block private/LAN targets for DAV pull (SSRF strict mode) |
| `SELFMAILER_JWT_ALGORITHM` | – | `HS256` | `HS256` / `HS384` / `HS512` |

### 📱 Android app

A real native client (`android/SelfMailer.apk`) — **not** a web-view wrapper. Built like Synology MailPlus:

- **Hamburger drawer** → account switcher + special folders + sub-folders, with unread badges
- **Bottom navigation**: Mail · Calendar · Notes
- HTML mail rendering, attachment download/open, search, compose/reply/forward
- **Biometric app-lock** (face / fingerprint / device PIN), unread accent markers
- **Self-hosted push** (ntfy) with per-account per-folder selection
- Server URL entered on first launch; talks to the same `/api/v1` as the web UI

### 🔌 API overview (`/api/v1`, JWT Bearer)

| Group | Endpoints (excerpt) |
|---|---|
| **Auth** | `auth/status`, `auth/setup`, `auth/login`, `auth/login/totp`, `auth/me`, `auth/totp/*` |
| **Accounts** | `accounts` (CRUD), `accounts/{id}/test` |
| **Mail** | `mail/{id}/folders[/counts]`, `mail/{id}/messages`, `…/{uid}`, `…/flags`, `…/move`, `…/send`, `…/transfer`, `…/batch-*` |
| **Organizer** | `calendar/events`, `contacts`, `notes`, `tasks` |
| **DAV / Feeds** | `dav/accounts`, `feeds/token`, `calendar/export.ics`, `contacts/export.vcf` |
| **Push** | `push` (ntfy config), `push/folders` (per-account folder selection) |
| **Dashboard** | `dashboard/summary` (bundled unseen counts) |

### 🧱 Tech stack

| Part | Tech |
|---|---|
| Backend | FastAPI · SQLModel · SQLite (WAL) · httpx · aiosmtplib · imap-tools |
| Web | React · Vite · TypeScript |
| App | Kotlin · Jetpack Compose · OkHttp · WorkManager · BiometricPrompt |
| Push | ntfy (self-hosted) |
| Deploy | Docker (multi-stage, non-root) · GHCR · Unraid |

### 🛠️ Development

```bash
# Backend
cd backend && pip install -r requirements.txt
export SELFMAILER_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
uvicorn app.main:app --reload --port 8090

# Web (second terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies /api → :8090
```

Interactive API docs: `http://localhost:8090/docs`

### 🗺️ Roadmap

- [x] Mail (IMAP/POP3/SMTP) · stacked accounts · folder tree + unread counts
- [x] Calendar · Contacts · Notes · Tasks
- [x] CalDAV/CardDAV pull + ICS/vCard export feeds
- [x] 2FA / TOTP · filter rules · cross-account transfer
- [x] Native Android app (Synology-style, biometric lock)
- [x] Self-hosted push (ntfy), per account & per folder
- [ ] OAuth for Gmail / Outlook
- [ ] Calendar month grid in the app (currently agenda)
- [ ] HTTPS reverse-proxy guide
- [ ] Tests toward 80 % coverage

---

<a id="deutsch"></a>

## 🇩🇪 Deutsch

Ein einzelner Docker-Container, der **kein Mailserver** ist — sondern ein **Client** für die Postfächer, die du schon hast. Er verbindet sich mit jedem **IMAP-/POP3**-Konto zum Lesen und mit **SMTP** zum Senden und bringt obendrauf **Kalender, Kontakte, Notizen und Aufgaben** mit. Eine Web-Oberfläche **und** eine native Android-App reden mit **einer JSON-API**.

Teil der **Self**-Reihe (SelfAuthenticator, SelfArchiver, SelfDashboard …) — gleiches Design-System, gleicher Deploy-Stil (GHCR → Unraid).

### ✨ Funktionen

**📬 Mail**
- Mehrere **IMAP-/POP3-/SMTP**-Konten pro Nutzer
- **Thunderbird-Ansicht**: gestapelte Konten mit Ordnerbaum pro Konto und **Ungelesen-Zählern**
- **HTML-Mails** lesen (sandboxed), **Anhänge** öffnen/herunterladen, **SPF/DKIM/DMARC**-Spoofing-Check
- **Schreiben / Antworten / Weiterleiten**, **Signatur** pro Konto, Entwürfe, Lese-/Empfangsbestätigung
- **Stern (Flag)**, gelesen/ungelesen, löschen, **verschieben**, **Konto-übergreifend übertragen**, **Sammelaktionen**
- **Filterregeln** (verschieben/markieren beim Eingang), **Postfach-Migration** serverseitig
- **Suche**, Pagination, Vorschauen und ein lokaler **SQLite-Cache**, den ein Hintergrund-Sync warm hält → die UI wartet nie auf einen langsamen Provider

**📅 Organizer**
- **Kalender** — lokale Termine, Monats-/Agenda-Ansicht, Geburtstage aus Kontakten
- **Kontakte** — reiches Adressbuch (Telefon, Adresse, Firma, Website, Geburtstag)
- **Notizen** & **Aufgaben**
- **CalDAV-/CardDAV**-Pull von externen Servern (Nextcloud, Synology …) + abonnierbare **ICS-/vCard**-Export-Feeds

**🔔 Benachrichtigungen (self-hosted, kein Google)**
- Push bei neuer Mail über **ntfy** — der Server pusht, dein Handy bekommt es, kein Firebase/FCM
- **Pro Konto, pro Ordner** — wähle genau, welche Postfächer dich benachrichtigen
- Einstellbar in **Web-UI und App** (eine gemeinsame Einstellung)

**🔐 Sicherheit & Plattform**
- **Multi-User** mit Admin-/Nutzer-Rollen; Admin kann Konten für Nutzer vorkonfigurieren
- **2FA / TOTP**-Login mit Backup-Codes
- Konto-Zugangsdaten **Fernet-verschlüsselt at-rest**
- **Native Android-App** (Kotlin + Jetpack Compose) mit Biometrie-App-Sperre
- **i18n DE/EN**, helles/dunkles Theme + eigene Akzentfarben
- **Ein Container** — FastAPI + SQLite, keine externe Datenbank

### 🧭 Architektur

```
        ┌──────────────┐          ┌───────────────┐
        │  Web-UI      │          │  Android-App  │
        │ React + Vite │          │ Kotlin/Compose│
        └──────┬───────┘          └──────┬────────┘
               └────────  JSON-API  ──────┘
                      /api/v1 · JWT (Bearer)
                          │
                 ┌────────▼─────────┐
                 │   FastAPI-Kern   │  SQLite (Cache + Konfig)
                 │  + Hintergrund-  │  Fernet-verschlüsselte Secrets
                 │      Sync        │
                 └────────┬─────────┘
        ┌─────────────────┼────────────────┬─────────────────┐
     IMAP / POP3        SMTP        CalDAV / CardDAV         ntfy
   (Postfächer lesen)(Mail senden)(Kal. & Kontakte sync) (Push bei neuer Mail)
```

### 🔔 Benachrichtigungen einrichten (ntfy)

Self-hosted Push ohne Google. Der SelfMailer-Server erkennt neue Mail und postet an **deinen** ntfy-Server; die ntfy-App auf dem Handy zeigt sie.

1. **ntfy-Container** auf Unraid starten (Port z. B. `8095`, `NTFY_BASE_URL=http://<host>:8095`).
2. **ntfy-App** aufs Handy, Server eintragen, das in SelfMailer angezeigte **Thema abonnieren**.
3. In SelfMailer (**Web** *Benutzer-Menü → 🔔 Benachrichtigungen* **oder App** *Einstellungen → ntfy-Push*): ntfy-URL eintragen → **Speichern & aktivieren** → pro Konto die Ordner wählen, die benachrichtigen sollen.
4. *(Optional)* `SELFMAILER_SYNC_INTERVAL=120` für ~2 Minuten Push-Latenz.

> Die App bietet alternativ einen **Vordergrund-Prüfmodus** (~1 Min, keine Extra-Infrastruktur) — der hält eine kleine Dauer-Benachrichtigung, die ntfy vermeidet.

### 🚀 Schnellstart (Docker)

```bash
cp .env.example .env
# Secret erzeugen und in .env eintragen (SELFMAILER_SECRET):
python -c "import secrets; print(secrets.token_hex(32))"

docker compose up -d
```

`http://<host>:8090` öffnen → das **Erst-Setup** legt den Admin-Account an → danach Mailkonten hinzufügen.

### 📦 Unraid

Template hinzufügen über
`https://raw.githubusercontent.com/kabelsalatundklartext/selfmailer/main/unraid/selfmailer.xml`
oder unter *Docker → Add Container → Template* importieren. **Master Secret** eintragen, Rest auf Standard lassen.

> **Rechte:** der Container läuft **non-root** (uid `99` / gid `100` = `nobody:users`). Gehört der appdata-Ordner noch root, einmal korrigieren:
> `chown -R 99:100 /mnt/user/appdata/selfmailer` (oder *Tools → Docker Safe New Permissions*).

### ⚙️ Konfiguration (ENV, Prefix `SELFMAILER_`)

| Variable | Pflicht | Default | Zweck |
|---|---|---|---|
| `SELFMAILER_SECRET` | ✅ | – | JWT-Signatur **und** At-Rest-Verschlüsselung der Postfach-Passwörter (≥ 32 Zeichen). Ändern macht gespeicherte Passwörter unlesbar. |
| `SELFMAILER_DB_PATH` | – | `/data/selfmailer.db` | SQLite-Pfad |
| `SELFMAILER_ADMIN_TOKEN` | – | – | Wenn gesetzt, verlangt das Erst-Setup diesen Token |
| `SELFMAILER_BASE_URL` | – | – | Öffentliche Basis-URL (z. B. für Feed-Links) |
| `SELFMAILER_SYNC_INTERVAL` | – | `300` | Sekunden zwischen Hintergrund-Syncs (kleiner = schnellerer Push) |
| `SELFMAILER_SYNC_DISABLE` | – | `0` | Hintergrund-Sync abschalten |
| `SELFMAILER_IMAP_TIMEOUT` | – | `15` | IMAP-Socket-Timeout (Sekunden) |
| `SELFMAILER_DAV_BLOCK_PRIVATE` | – | `false` | Private/LAN-Ziele beim DAV-Pull blocken (SSRF-Strikt-Modus) |
| `SELFMAILER_JWT_ALGORITHM` | – | `HS256` | `HS256` / `HS384` / `HS512` |

### 📱 Android-App

Ein echter nativer Client (`android/SelfMailer.apk`) — **kein** WebView-Wrapper. Gebaut wie Synology MailPlus:

- **Hamburger-Schublade** → Konto-Wechsler + Sonderordner + Unterordner, mit Ungelesen-Badges
- **Untere Navigation**: Mail · Kalender · Notizen
- HTML-Mail-Anzeige, Anhänge öffnen/herunterladen, Suche, Schreiben/Antworten/Weiterleiten
- **Biometrie-App-Sperre** (Gesicht / Finger / Geräte-PIN), Akzent-Markierung für Ungelesene
- **Self-hosted Push** (ntfy) mit Auswahl pro Konto und Ordner
- Server-URL beim ersten Start; spricht dieselbe `/api/v1` wie die Web-UI

### 🔌 API-Überblick (`/api/v1`, JWT Bearer)

| Bereich | Endpunkte (Auszug) |
|---|---|
| **Auth** | `auth/status`, `auth/setup`, `auth/login`, `auth/login/totp`, `auth/me`, `auth/totp/*` |
| **Konten** | `accounts` (CRUD), `accounts/{id}/test` |
| **Mail** | `mail/{id}/folders[/counts]`, `mail/{id}/messages`, `…/{uid}`, `…/flags`, `…/move`, `…/send`, `…/transfer`, `…/batch-*` |
| **Organizer** | `calendar/events`, `contacts`, `notes`, `tasks` |
| **DAV / Feeds** | `dav/accounts`, `feeds/token`, `calendar/export.ics`, `contacts/export.vcf` |
| **Push** | `push` (ntfy-Konfig), `push/folders` (Ordnerauswahl pro Konto) |
| **Dashboard** | `dashboard/summary` (gebündelte Ungelesen-Zähler) |

### 🧱 Technik-Stack

| Teil | Technik |
|---|---|
| Backend | FastAPI · SQLModel · SQLite (WAL) · httpx · aiosmtplib · imap-tools |
| Web | React · Vite · TypeScript |
| App | Kotlin · Jetpack Compose · OkHttp · WorkManager · BiometricPrompt |
| Push | ntfy (self-hosted) |
| Deploy | Docker (Multi-Stage, non-root) · GHCR · Unraid |

### 🛠️ Entwicklung

```bash
# Backend
cd backend && pip install -r requirements.txt
export SELFMAILER_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
uvicorn app.main:app --reload --port 8090

# Web (zweites Terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173, proxyt /api → :8090
```

Interaktive API-Doku: `http://localhost:8090/docs`

### 🗺️ Roadmap

- [x] Mail (IMAP/POP3/SMTP) · gestapelte Konten · Ordnerbaum + Ungelesen-Zähler
- [x] Kalender · Kontakte · Notizen · Aufgaben
- [x] CalDAV-/CardDAV-Pull + ICS-/vCard-Export-Feeds
- [x] 2FA / TOTP · Filterregeln · Konto-übergreifend übertragen
- [x] Native Android-App (Synology-Stil, Biometrie-Sperre)
- [x] Self-hosted Push (ntfy), pro Konto & pro Ordner
- [ ] OAuth für Gmail / Outlook
- [ ] Kalender-Monatsgitter in der App (aktuell Agenda)
- [ ] HTTPS-Reverse-Proxy-Anleitung
- [ ] Tests Richtung 80 % Coverage

---

<div align="center">

**SelfMailer** · Teil der Self-Reihe · made with 📬 for self-hosting

</div>
