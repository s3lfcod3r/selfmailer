# SelfMailer 1.95.3: Mail-Laufzeiten diagnostizieren

Diese Messung grenzt langsames Öffnen/Löschen und langsame Ordnerlisten ein.
Sie ist **keine weitere Gmail-Reparatur**. Web und Android benutzen dieselben
Server-Endpunkte; für diese reine Backend-Messung ist keine neue APK nötig.

## Aktivieren und begrenzt testen

Container auf `ghcr.io/s3lfcod3r/selfmailer:1.95.3` aktualisieren und Version
`1.95.3` unter `/api/health` prüfen. Ein GitHub-Release aktualisiert keinen
laufenden Container. Android 1.95.1 / Build 104 bleibt kompatibel; SelfStore
behält diese APK. Das Server-Release enthält keine neue APK.

Nach Deployment dieses Codes schreibt der Server pro ausgewählter Anfrage
höchstens eine Zeile `mail_timing {JSON}` ins Container-Log (Log-Level WARNING).
Standard: `SELFMAILER_MAIL_TIMING=1`, Schwelle
`SELFMAILER_MAIL_TIMING_SLOW_MS=1000`. Langsame Anfragen und Fehler werden erfasst;
Fehler eines abgefangenen, gemessenen Teilschritts auch bei HTTP 200.

Für einen kurzen Test die Schwelle auf `0` setzen und den Container mit dieser
Umgebung neu erstellen. Dann dieselbe eigene Testmail erstmals und nochmals
öffnen, einmal in den Papierkorb verschieben und dort nachsehen. Eine unklare
oder langsame Löschung nicht blind wiederholen; keine echten Mails dauerhaft
löschen. Anschließend Schwelle wieder auf `1000` oder Messung mit
`SELFMAILER_MAIL_TIMING=0` abschalten. Ungültige Schwellen fallen auf 1000 zurück.
Umgebungsvariablen müssen den Container erreichen; eine Host-`.env` allein wird
nicht automatisch in jeden Container übernommen.

Nur die **neuen `mail_timing`-Zeilen** des kurzen Testfensters exportieren, nicht
das gesamte Log. Beispiel in der Server-Shell, nach Prüfung des Container-Namens:

```sh
docker logs --since 10m <container-name> 2>&1 | grep 'mail_timing {'
```

Eine Konto-ID kann dem angemeldeten eigenen Konto über vorhandene Verwaltungs-
ansichten zugeordnet werden. Keine Tokens oder Passwörter zum Log hinzufügen.
Die Messung stellt keinen neuen HTTP-Diagnose-Endpunkt bereit.

## Felder und Interpretation

- `request_id`: zufällige serverseitige Kennung je Anfrage; übernimmt keine
  vom Client gelieferte Kennung. Parallele Vorgänge bleiben unterscheidbar.
- `account_id`, `operation`: numerische interne Konto-ID und `list`, `read`,
  `delete` oder `sync`. Nur nach erfolgreicher bestehender Eigentümerprüfung.
- `duration_ms`: Serverzeit ab Mess-Middleware bis zu den Antwort-Headern.
  Enthält Abhängigkeiten/Threadpool-Warten, nicht Browser-Rendering, den Weg vom
  Client zum Server, vorgeschaltete Warteschlangen oder die Body-Übertragung.
- `status`: HTTP-Status; `null`, wenn keine Antwort die Middleware erreichte.
- `failed`: HTTP-Fehler, unbehandelte Ausnahme oder mindestens ein gemessener
  fehlgeschlagener Teilschritt. Bedeutet bei HTTP 200 nicht zwingend, dass die
  Gesamtaktion fehlgeschlagen ist (z. B. erfolgreicher Cache-Fallback).
- `phases`: pro Schritt `ms` (aufsummiert), `count`, `failed` (Fehleranzahl).
  Nicht ausgeführte Schritte fehlen; das ist **keine Nullzeitmessung**.
- `source`: bei Lesen/Listen Quelle des ausgelieferten Ergebnisses: `cache`,
  `imap`, `cached_fallback`, `header_fallback` oder `miss`. Ein Cache-Listen-
  ergebnis kann zuvor einen synchronen IMAP-Abgleich gebraucht haben.
- `result`: bei Löschen `moved` oder `deleted`, bei belegtem Sync `busy`.
  `moved` bestätigt nur den erfolgreichen Aufruf; es ist **kein unabhängiger
  Nachweis**, dass die Mail später in einer anderen Ordneransicht sichtbar ist.
- `move_mode`: beim Verschieben `native` (IMAP MOVE) oder `copy_delete`
  (Bibliotheks-Fallback COPY/STORE/EXPUNGE), bei unbekannten Fähigkeiten `unknown`.
  Die Messung sendet dafür keine zusätzlichen IMAP-Befehle.

| Schritt | Was dort gewartet/gearbeitet wird |
| --- | --- |
| `pool_wait` | Freie Verbindung erhalten, einschließlich Kontingent-Sperre |
| `connect`, `login` | MailBox-Aufbau (u. a. TCP/TLS/Begrüßung), Anmeldung |
| `condstore`, `select`, `logout` | CONDSTORE aktivieren, Ordner auswählen, Verbindung schließen |
| `uidvalidity`, `folder_list` | Nachrichtengeneration prüfen, Papierkorb ermitteln |
| `fetch_body`, `parse_body` | Nachricht abrufen einschließlich Verarbeitung; Verarbeitung als Teilmessung |
| `move`, `delete` | Mail verschieben bzw. bestehender endgültiger Löschpfad |
| `cache_read`, `cache_write`, `cache_hide` | Mail-Cache lesen, schreiben, ausgeblendete UID speichern |
| `cache_list`, `cache_header`, `cache_generation` | Liste, Ersatzvorschau, gespeicherte Nachrichtengeneration |
| `sync_total`, `folder_lock_wait` | Synchroner Ordnerabgleich insgesamt; Warten auf dessen Sperre |
| `folder_status`, `uid_search` | Ordnerstatus und UID-Suche beim Sync |
| `account_lookup`, `notify` | Eigentümerprüfung/DB-Zugriff; Benachrichtigung an den Ereignisbus |

**Teilzeiten nicht blind addieren:** `parse_body` steckt in `fetch_body`;
`sync_total` enthält unter anderem Pool-, Verbindungs- und Statuszeiten.
Nicht jeder Sync-Teilschritt ist instrumentiert. Hohe Restzeit kann weitere
Eingrenzung verlangen; sie beweist allein weder Gmail- noch SQLite-Probleme.
Hintergrund-Scheduler, Füll-Worker und Pool-Aufräumer werden nicht an die
Anfrage-Messung angehängt. Ihre Konkurrenz zeigt sich ggf. im Warten auf Sperren.
Mehrere Server-Worker teilen weder Pool noch Messkontext.

## Datenschutz und unveränderte Schutzregeln

Die neuen Zeilen enthalten keine Mailadressen, Hostnamen, Ordnernamen, UIDs,
Betreffzeilen, Inhalte, Anhänge, Zugangsdaten, Roh-URLs oder Ausnahme-Texte.
Feldnamen/Marker sind fest begrenzt. IDs und Zeitpunkte bleiben dennoch
Nutzungsmetadaten: Logs nur für berechtigte Administratoren zugänglich machen
und nicht unnötig lange aufbewahren. Log-Rotation ist Aufgabe des Deployments;
diese Messung legt keinen eigenen persistenten Speicher an.

Bestehende Access-/Fehlerlogs können unabhängig davon sensible Angaben enthalten.
Die Datenschutz-Aussage gilt ausdrücklich **nur für die neuen Messzeilen**.
Authentifizierung, Kontozugriff, UIDVALIDITY-Prüfung, Löschlogik, Timeouts,
Verbindungslimits und API-Antworten bleiben unverändert. Ein Logging-Fehler wird
abgefangen und löst insbesondere keine Wiederholung einer Schreibaktion aus.
