# SelfMailer 1.96.0 — Sicherheitsupdate nach der Durchsicht vom 27.09.2026

Dieses Update setzt die Befunde der dritten Code-Durchsicht um. Es betrifft
**Backend, Web-Oberfläche, Docker-Build und die Unraid-Vorlage**. Die Android-App
(Build 105, 1.95.5) bleibt kompatibel — ihre Quellen liegen außerhalb dieses Repos.

Es gibt **eine Änderung, die Handarbeit verlangt**: die Abo-URLs mit Feed-Token.
Details unter „Was du nach dem Update tun musst".

---

## 1. Anmeldung lässt sich jetzt wirklich widerrufen

Bis 1.95.5 blieb ein gestohlenes JWT bis zu sieben Tage gültig — auch nach einem
Passwortwechsel. Jetzt trägt jeder Nutzer eine `token_version`; sie steht in den
Claims und wird bei jeder Anfrage verglichen. Passwort ändern und TOTP abschalten
erhöhen sie, damit werden **alle** bereits ausgegebenen Tokens sofort ungültig.

Nebenwirkung: Nach dem Update und nach jedem Passwortwechsel müssen sich Web und
Android-App **neu anmelden**. Das ist gewollt.

Tokens tragen zusätzlich `iss` und `aud`. Alte Tokens ohne diese Felder gelten
bis zu ihrem Ablauf weiter, damit das Update niemanden aussperrt.

## 2. Feed-Token: nicht mehr im Log, nicht mehr im Klartext in der DB

Der alte Zustand: das Token stand als Query-Parameter in jeder Abo-URL, uvicorn
schrieb die komplette Request-Line ins Access-Log (in 24 Stunden 16.366 Mal), und
in der Datenbank lag es im Klartext, unbegrenzt gültig.

Neu:

- **Log-Filter** (`core/logfilter.py`): `token=`, `write_token=`, `access_token=`
  und `code=` werden im Zugriffslog maskiert, bevor die Zeile formatiert wird.
  Das wirkt sofort, ohne dass ein Client angepasst werden muss.
- **Header `X-Feed-Token`** wird zusätzlich zum Query-Parameter akzeptiert. Wer
  seinen Client anpassen kann, hält das Token damit ganz aus der URL heraus.
- In der Datenbank liegt nur noch der **SHA-256-Hash**. Der Klartext ist
  ausschließlich in der Antwort sichtbar, die ihn erzeugt hat.
- **Ablauf nach 180 Tagen** (`FEED_TOKEN_TTL_DAYS`) und `last_used_at`, damit man
  ungenutzte Tokens erkennt.
- **Rate-Limit** 120 Anfragen pro Minute je Token (`FEED_RATE_LIMIT`) auf den
  Feed-Endpunkten. Ein defektes Widget flutet damit nicht mehr den IMAP-Pool.
- Der **Lesepfad akzeptiert keinen Schreib-Token mehr**. Wer eine Kachel
  versehentlich mit dem Schreib-Token konfiguriert hatte, bekommt jetzt 401 —
  und trägt damit kein Token mit Kalender-Schreibrecht mehr in Abo-URLs.

Die Sync-Seite zeigt entsprechend: direkt nach dem Erzeugen oder Rotieren die
fertigen Abo-URLs mit dem Hinweis „nur jetzt sichtbar", danach nur noch
„Token vorhanden", Gültig-bis, Zuletzt-genutzt und den Rotieren-Knopf.

## 3. Mail-Ansicht: zweite Verteidigungslinie beim Lesen

- Das Mail-iframe hat jetzt **immer** eine CSP. Vorher verschwand sie, sobald der
  Nutzer „Bilder anzeigen" klickte; im Anzeige-Modus ist nur noch `img-src` offen.
- **Drucken** nimmt denselben aufbereiteten Rumpf und dieselbe CSP wie die
  Leseansicht. Vorher ließen sich über den Druckknopf Tracking-Pixel nachladen,
  obwohl die Bilder blockiert waren.
- **DOMPurify** läuft jetzt auch beim Lesen (`script`, `iframe`, `object`,
  `embed`, `form`, `base` sowie `srcdoc`/`formaction`/`ping` raus). Die Sandbox
  bleibt — sie ist ab sofort nicht mehr der einzige Schutz.
- Ein gemeinsamer Satz **Sandbox-Werte** statt drei verschiedener; keiner enthält
  `allow-scripts`, ein Test hält das fest.
- Die Erkennung externer Inhalte arbeitet über den DOM statt über reguläre
  Ausdrücke und erkennt damit auch protokollrelative URLs, `srcset` und
  CSS-`@import`.

## 4. IMAP/SMTP: DNS-Rebinding geschlossen, `imap_ssl` wirkt

Die Host-Prüfung löste den Namen auf und verwarf Loopback/Link-Local — verbunden
wurde danach aber wieder über den Namen. Ein Host, der bei der Prüfung öffentlich
auflöst und beim Verbinden auf `127.0.0.1` oder `169.254.169.254` zeigt, umging
den Schutz. Jetzt wird die geprüfte IP festgehalten und die Verbindung dorthin
aufgebaut; SNI und Zertifikatsname bleiben der Hostname. Dasselbe Verfahren nutzt
der DAV-Client schon länger.

Der Schalter `imap_ssl` wurde bisher nirgends gelesen — die Verbindung war immer
implizites TLS. Jetzt wirkt er: STARTTLS bei abgeschaltetem `imap_ssl`, wobei
Port 993 weiterhin Vorrang hat.

Außerdem: `folder`-Parameter werden zentral auf Steuerzeichen geprüft (CRLF),
Anhänge bekommen nur noch einen Content-Type aus einer Allowlist, erwartbare
Netzabbrüche landen als WARNING ohne Traceback im Log (vorher 42 Tracebacks in
24 Stunden), und Backup-Stunde wie Aufräumgrenze rechnen in UTC statt in der
Ortszeit des Containers.

## 5. Betrieb und Build

- **Unraid-Vorlage** hat jetzt `--restart=unless-stopped --memory=1g`. Ohne das
  lief SelfMailer nach einem Absturz oder Host-Neustart nicht wieder an.
- `npm ci || npm install` im Dockerfile ist weg — schlägt `npm ci` fehl, soll der
  Build fehlschlagen statt am Lockfile vorbei andere Versionen zu ziehen.
- `requirements.txt` hat Ober- und Untergrenzen für alle Pakete, dazu läuft
  `pip-audit` und `npm audit` im CI (vorerst nicht blockierend). Ein echtes
  Hash-Lockfile steht noch aus.
- Neue Tests prüfen, dass ein **Backup sich wirklich wieder öffnen lässt**
  (`PRAGMA integrity_check`, gleicher Inhalt, auch mit offenem Schreiber im WAL).

## 6. CORS: nur exakte Origins, niemals Wildcard

`SELFMAILER_CORS_ORIGINS` wird zusammen mit `allow_credentials=True` benutzt.
Damit gilt:

- **Richtig:** `https://mail.example.com,https://dashboard.example.com` —
  vollständige Origins mit Schema, Host und ggf. Port.
- **Falsch:** `*`, `null`, `http://*`, `https://*` oder ein Eintrag, der auf
  fremde Hosts passt. Eine Wildcard zusammen mit Credentials würde jeder fremden
  Seite erlauben, im Namen eines angemeldeten Nutzers zu lesen.

Seit 1.96.0 verwirft SelfMailer solche Einträge beim Start und schreibt eine
Warnung ins Log (`selfmailer.cors`). Der Dienst startet trotzdem — aber ohne den
unsicheren Eintrag. Wer eine Kachel auf einem fremden Origin betreibt und
plötzlich CORS-Fehler sieht, findet den Grund in dieser Logzeile.

HSTS setzt SelfMailer weiterhin bewusst nicht selbst; das gehört in den
Reverse Proxy (siehe `HTTPS.md`).

---

## Was du nach dem Update tun musst

1. **Neu anmelden** — in der Web-Oberfläche und in der Android-App. Alle alten
   Sitzungen sind ungültig.
2. **Abo-URLs neu holen.** Bestehende Kalender-/Kontakt-Abos funktionieren
   zunächst weiter (die alten Klartext-Tokens werden beim ersten Start in Hashes
   überführt), aber SelfMailer kann sie nie wieder anzeigen. Wer die URL nicht
   gespeichert hat: Sync-Seite → „Neu erzeugen", URL sofort kopieren und im
   Kalender/Adressbuch ersetzen. Die alte URL hört damit auf zu funktionieren.
3. **SelfDashboard-Kachel prüfen.** Nutzte sie den Schreib-Token für Lesezugriffe,
   bekommt sie jetzt 401. Lesen: Lese-Token. Termine anlegen/ändern/löschen:
   Schreib-Token. Am besten beide einmal rotieren und neu eintragen.
4. **Unraid-Vorlage neu einlesen**, damit `--restart=unless-stopped --memory=1g`
   wirksam wird. Ein reines Image-Update ändert die Container-Parameter nicht.
5. **`SELFMAILER_CORS_ORIGINS` kontrollieren** und danach einmal ins Log sehen, ob
   eine CORS-Warnung steht.

Vor dem Rollout wie immer: konsistentes DB-Backup ziehen, in einer Testinstanz
prüfen, dann erst den Server aktualisieren.
