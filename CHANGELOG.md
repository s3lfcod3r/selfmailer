# 📰 Changelog

Newest version first. Full release notes are linked per version (mostly in German).
Neueste Version zuerst. Die ausführlichen Release-Notes sind je Version verlinkt.

> **⚠️ Update tip / Update-Hinweis:** Update backend/web **and** the Android APK together. ·
> Backend/Web **und** Android-APK gemeinsam aktualisieren.

---

## 1.97.1 · Build 127
- 🇬🇧 **APK: new encrypted storage.** Login token, drafts and personal data now live in an own Android-Keystore store (AES-256-GCM, names hidden via HMAC) instead of the no-longer-maintained `EncryptedSharedPreferences`. Existing data is migrated automatically on first start; the old store is deleted only after a successful write.
- 🇬🇧 Web foundations updated: React 19, Vite 8, TypeScript 7 (no visible changes). Android source patches removed from the public repository.
- 🇩🇪 **APK: neuer verschlüsselter Speicher.** Login-Token, Entwürfe und persönliche Daten liegen jetzt in einem eigenen Android-Keystore-Speicher (AES-256-GCM, Namen per HMAC verschleiert) statt im nicht mehr gepflegten `EncryptedSharedPreferences`. Bestehende Daten werden beim ersten Start automatisch übernommen; der alte Speicher wird erst nach erfolgreichem Schreiben gelöscht.
- 🇩🇪 Web-Grundlagen aktualisiert: React 19, Vite 8, TypeScript 7 (keine sichtbaren Änderungen). Android-Quellpatches aus dem öffentlichen Repository entfernt.
- 📄 [RELEASE-1.97.1.txt](docs/RELEASE-1.97.1.txt)

## 1.97.0 · Build 125
- 🇬🇧 **APK:** HTTPS with an own CA (user-installed certificates are trusted), mail view no longer loads external CSS/fonts without permission, printing no longer loads blocked remote images (tracking).
- 🇩🇪 **APK:** HTTPS mit eigener CA (vom Nutzer installierte Zertifikate), Mail-Ansicht lädt ohne Freigabe kein externes CSS/keine Schriften mehr, Drucken lädt keine blockierten externen Bilder mehr (Tracking).
- 🇬🇧 **Security release.** IMAP certificates are now verified (before: any certificate was accepted), with an optional per-account fingerprint for self-signed LAN servers · CSRF protection for cookie sessions · scheduled mails encrypted at rest, backups/DB readable by the container user only · certifi without the compromised emSign CA.
- 🇬🇧 **HTTP stays the default, HTTPS is your choice:** cookie mode `auto` (HTTP in the LAN and HTTPS side by side), reverse-proxy support (`SELFMAILER_TRUSTED_PROXIES`), optional HSTS, optional HTTPS directly in the container (port 8443).
- 🇬🇧 Operations: configurable backup folder, app code read-only in the container, blocking dependency audits + weekly audit + Dependabot.
- 🇩🇪 **Sicherheitsrelease.** IMAP-Zertifikate werden geprüft (vorher wurde jedes akzeptiert), optional mit Fingerprint pro Konto für selbstsignierte LAN-Server · CSRF-Schutz für Cookie-Sitzungen · geplante Mails verschlüsselt, Backups/DB nur für den Container-Nutzer lesbar · certifi ohne die kompromittierte emSign-CA.
- 🇩🇪 **HTTP bleibt Standard, HTTPS ist wählbar:** Cookie-Modus `auto` (HTTP im LAN und HTTPS gleichzeitig), Reverse-Proxy-Unterstützung (`SELFMAILER_TRUSTED_PROXIES`), optional HSTS, optional HTTPS direkt im Container (Port 8443).
- 🇩🇪 Betrieb: Backup-Ordner wählbar, App-Code im Container schreibgeschützt, blockierende Abhängigkeitsprüfung + wöchentlicher Audit + Dependabot.
- 📄 [RELEASE-1.97.0.txt](docs/RELEASE-1.97.0.txt)

## 1.96.9 · Build 123
- 🇬🇧 Cleanup dialog reworked (web + APK): search and results scroll separately from the fixed action bar; preview appears right after searching; web checkboxes sit next to their labels again.
- 🇩🇪 Aufräumdialog überarbeitet (Web + APK): Suche und Trefferliste scrollen getrennt vom festen Aktionsbereich; Vorschau direkt nach der Suche; Checkboxen im Web stehen wieder neben ihrem Text.
- 📄 [RELEASE-1.96.9.txt](docs/RELEASE-1.96.9.txt)

## 1.96.8 · Build 121
- 🇬🇧 Optional automatic **two-way contact sync** for explicitly selected, linked contacts — with duplicate check and conflict detection.
- 🇩🇪 Optionaler automatischer **Kontakt-Sync in beide Richtungen** für ausgewählte, verknüpfte Kontakte — mit Duplikatprüfung und Konflikterkennung.
- 📄 [RELEASE-1.96.8.txt](docs/RELEASE-1.96.8.txt)

## 1.96.7 · Build 119
- 🇬🇧 **Export SelfMailer contacts to the phone** with selection, search filter, duplicate preview and explicit confirmation. Existing phone contacts are never overwritten.
- 🇩🇪 **SelfMailer-Kontakte aufs Handy exportieren** mit Auswahl, Suchfilter, Duplikatvorschau und ausdrücklicher Bestätigung. Vorhandene Handykontakte werden nie überschrieben.
- 📄 [RELEASE-1.96.7.txt](docs/RELEASE-1.96.7.txt)

## 1.96.6 · Build 117
- 🇬🇧 Fixed phone-contact import in the APK: the import button now lives in a fixed bottom bar (was cut off on small screens).
- 🇩🇪 Handykontaktimport in der APK korrigiert: der Import-Button liegt jetzt in der festen unteren Leiste (war auf kleinen Bildschirmen abgeschnitten).
- 📄 [RELEASE-1.96.6.txt](docs/RELEASE-1.96.6.txt)

## 1.96.5 · Build 115
- 🇬🇧 Dedicated **contacts area** in the APK; selective phone-contact import with duplicate detection; calendar agenda (today + 7 days) with reminders.
- 🇩🇪 Eigener **Kontaktbereich** in der APK; gezielter Handykontakt-Import mit Duplikaterkennung; Kalender-Agenda (heute + 7 Tage) mit Erinnerungen.
- 📄 [RELEASE-1.96.5.txt](docs/RELEASE-1.96.5.txt)

## 1.96.4 · Build 113
- 🇬🇧 **Mailbox cleanup**: find similar mail by sender, domain, subject, keyword or label; preview before moving; saved search views; rules for future mail.
- 🇩🇪 **Postfach aufräumen**: ähnliche Mails nach Absender, Domain, Betreff, Stichwort oder Label finden; Vorschau vor dem Verschieben; gespeicherte Suchansichten; Regeln für künftige Mails.
- 📄 [RELEASE-1.96.4.txt](docs/RELEASE-1.96.4.txt)

## 1.96.3 · Build 111
- 🇬🇧 Unsent Android **drafts are kept** (encrypted locally); list position remembered; quick filters and conversation view.
- 🇩🇪 Ungesendete Android-**Entwürfe bleiben erhalten** (lokal verschlüsselt); Listenposition wird gemerkt; Schnellfilter und Unterhaltungsansicht.
- 📄 [RELEASE-1.96.3.txt](docs/RELEASE-1.96.3.txt)

## 1.96.2 · Build 109
- 🇬🇧 🔒 Logout permanently revokes the session; transactional data cleanup; DAV credentials stay bound to their origin; updated APK.
- 🇩🇪 🔒 Logout widerruft die Sitzung dauerhaft; transaktionale Datenbereinigung; DAV-Zugangsdaten bleiben am Origin; aktualisierte APK.
- 📄 [RELEASE-1.96.2.txt](docs/RELEASE-1.96.2.txt)

## 1.96.0 · September 2026 🔒
- 🇬🇧 Security update after the third code review: revocable logins, feed tokens stored as hashes, CSP/DOMPurify on the read path, IMAP/SMTP IP pinning. **Requires re-login and new subscription URLs.**
- 🇩🇪 Sicherheitsupdate nach der dritten Code-Durchsicht: widerrufbare Anmeldungen, Feed-Tokens als Hash, CSP/DOMPurify beim Lesen, IMAP/SMTP-IP-Pinning. **Neu anmelden und Abo-URLs neu einrichten.**
- 📄 [SICHERHEIT-1.96.0.md](docs/SICHERHEIT-1.96.0.md)

## 1.95.5 · Build 105
- 🇬🇧 Gmail/IMAP: backoff after "busy" responses, bounded live counts, fewer IMAP round trips when opening a message.
- 🇩🇪 Gmail/IMAP: Wartezeit nach „busy“-Antworten, begrenzte Live-Zählungen, weniger IMAP-Befehle beim Öffnen einer Nachricht.
- 📄 [GMAIL-SYNC-1.95.5.md](docs/GMAIL-SYNC-1.95.5.md)

## 1.95.4
- 🇬🇧 Post-login IMAP capabilities are detected, so native MOVE and CONDSTORE are used.
- 🇩🇪 IMAP-Funktionen nach dem Login werden erkannt, dadurch werden natives MOVE und CONDSTORE genutzt.
- 📄 [IMAP-CAPABILITIES.md](docs/IMAP-CAPABILITIES.md)

## 1.95.3
- 🇬🇧 Privacy-preserving mail timing diagnostics (server only, no new APK).
- 🇩🇪 Datensparsame Diagnose der Mail-Laufzeiten (nur Server, keine neue APK).
- 📄 [MAIL-TIMING.md](docs/MAIL-TIMING.md)

## 1.95.2
- 🇬🇧 IMAP connection-pool fixes for Gmail and other providers (server only, APK 1.95.1 stays compatible).
- 🇩🇪 Korrekturen am IMAP-Verbindungspool für Gmail und andere Anbieter (nur Server, APK 1.95.1 bleibt kompatibel).
- 📄 [GMAIL-POOL-1.95.2.md](docs/GMAIL-POOL-1.95.2.md)

## 1.95.1 · Build 104
- 🇬🇧 Bugfix and security update for web and backend: safer note drafts, lighter note overview.
- 🇩🇪 Bugfix- und Sicherheitsupdate für Web und Backend: sicherere Notizentwürfe, schlankere Notizübersicht.
- 📄 [BROWSER-FIXES-1.95.1.md](docs/BROWSER-FIXES-1.95.1.md)

## 1.95.0 · Build 103
- 🇬🇧 Performance for Gmail and slow IMAP accounts (sync time budget, connection pool usage).
- 🇩🇪 Performance für Gmail und langsame IMAP-Konten (Zeitbudget beim Sync, Nutzung des Verbindungspools).
- 📄 [GMAIL-PERFORMANCE-1.95.0.md](docs/GMAIL-PERFORMANCE-1.95.0.md)

## 1.94.0 · September 2026 🔒
- 🇬🇧 Coordinated security and performance fixes for backend, web and Android (mail identity via UIDVALIDITY + UID).
- 🇩🇪 Koordinierte Sicherheits- und Performance-Korrekturen für Backend, Web und Android (Mail-Identität über UIDVALIDITY + UID).
- 📄 [SECURITY-UPDATE-2026-09.md](docs/SECURITY-UPDATE-2026-09.md)
