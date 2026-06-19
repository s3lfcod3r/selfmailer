# SelfMailer

Self-hosted, **Multi-User-E-Mail-Client** (kein Mailserver) mit WebUI und
geplanter Android-APK. Bindet bestehende **IMAP/POP3/SMTP**-Konten an und bringt
**Kalender, Kontakte und Notizen** mit. Teil der **Self**-Projektreihe.

> Status: **lauffähig** – Auth & User-Verwaltung, verschlüsselter Konto-Store,
> Mail lesen/senden, Notizen, Kalender, Kontakte sowie CalDAV/CardDAV-Sync und
> abonnierbare ICS/vCard-Export-Feeds.

## Stack
- **Backend:** FastAPI (Python), SQLite, SQLModel
- **Frontend:** React + Vite (TypeScript), Self-Brand-Kit
- **Deployment:** Single Docker-Container, GHCR + Unraid

## Funktionen (aktuell)
- First-Run-Setup → erster Admin
- Login (JWT), Rollen Admin/User
- Admin: User anlegen/sperren/löschen, Mailkonten für User vorkonfigurieren
- Mailkonten anlegen (Self-Service), Zugangsdaten **verschlüsselt at-rest**
- Mail: Posteingang lesen, Nachricht öffnen, schreiben/antworten/weiterleiten (SMTP)
- **Notizen:** anlegen, anheften, löschen
- **Kalender & Kontakte:** lokaler Store (CRUD, Suche)
- **Sync & Export:**
  - Abonnierbare Feeds `GET /api/v1/calendar/export.ics` und `/contacts/export.vcf`
    (Auth per Feed-Token in der URL oder Bearer; Token rotierbar)
  - Externe **CalDAV/CardDAV**-Konten anbinden und read-only in den lokalen
    Store spiegeln (Server-Passwort verschlüsselt at-rest)

## Lokal starten

### Backend
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
.venv/Scripts/python -m pip install -r requirements.txt
# Secret setzen (PowerShell):  $env:SELFMAILER_SECRET="<langes-zufaelliges-secret>"
.venv/Scripts/python -m uvicorn app.main:app --port 8090 --reload
```
API-Doku: http://localhost:8090/docs

### Frontend
```bash
cd frontend
npm install
npm run dev   # http://localhost:5173 (proxyt /api an :8090)
```

### Docker (Single-Container, Frontend + Backend)
```bash
# Build (Context = Repo-Root, Dockerfile in backend/)
docker build -f backend/Dockerfile -t selfmailer .
docker run -d --name selfmailer -p 8090:8090 \
  -e SELFMAILER_SECRET="<langes-zufaelliges-secret>" \
  -v selfmailer-data:/data selfmailer
# WebUI/API: http://localhost:8090/
```
Fertige Images: `ghcr.io/kabelsalatundklartext/selfmailer:latest` (per GitHub
Actions gebaut). Für Unraid liegt ein Template unter [`unraid/selfmailer.xml`](unraid/selfmailer.xml).

## Sicherheit
- Mailkonto-Passwörter Fernet-verschlüsselt (Key aus `SELFMAILER_SECRET`).
- Login-Passwörter Argon2-gehasht.
- TLS zu Mailservern (IMAPS/SMTPS/STARTTLS).
- **`SELFMAILER_SECRET` niemals committen.** Änderung macht gespeicherte
  Konto-Passwörter unbrauchbar (müssen neu eingegeben werden).

## Roadmap
Siehe [docs/KONZEPT.md](docs/KONZEPT.md). Als Nächstes: OAuth (Gmail/Outlook)
statt App-Passwörtern, echte CalDAV/CardDAV-Tests gegen einen realen Server,
und die Android-APK (eigener Code) mit optionalem WireGuard-Split-Tunnel.
