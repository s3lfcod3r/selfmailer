# Gmail/IMAP – 1.95.5 (Web, Backend und Android)

Dieses Update behebt zusätzliche Last nach einer Busy-Antwort und reduziert
unnötige IMAP-Befehle beim Öffnen einer Nachricht. Es ist keine Garantie für
eine bestimmte Gmail-Antwortzeit; die verbleibende Provider-/Kontolatenz muss
nach Installation erneut gemessen werden.

## Busy ist kein erfolgreicher Abgleich

Web und Android unterscheiden jetzt einen erfolgreichen Sync von HTTP 200 mit
`busy:true`. Ein Busy-Ergebnis startet keine anschließende Vorladung oder
Live-Zählung. Die vorhandene Nachrichtenliste bleibt sichtbar.

- Web: Wartezeit pro Konto/Ordner 20 bis 120 Sekunden bei wiederholtem Busy,
  bis 300 Sekunden bei Fehlern. Keine parallelen Syncs desselben Ordners.
- Android: 25 bis 120 Sekunden bei Busy, bis 300 Sekunden bei Fehlern,
  ebenfalls mit Sperre gegen parallele Syncs. Abbruch bleibt ein Abbruch.
- Manuelles Neuladen darf die Wartezeit umgehen, nicht eine bereits laufende
  Anfrage duplizieren. Andere Konten und Ordner haben unabhängige Wartezeiten.
- Serverereignisse aktualisieren die Web-Liste aus dem Cache, ohne einen
  weiteren IMAP-Sync auszulösen. Automatisches Polling bleibt erhalten.

## Ordnerzählungen

Der schnelle 20-Sekunden-Web-Takt zählt nicht mehr zusätzlich alle Ordner live.
Normale Ereignis-/Aktions-Updates lesen die gecachten Zähler; manuelle
Kontoaktualisierung und das längere konfigurierte Polling können live zählen.

Backend und Hintergrund-Scheduler teilen sich eine nicht wartende Sperre je
Konto. Höchstens eine vollständige Live-Zählung läuft gleichzeitig, nach ihrem
Ende gilt eine Pause von 30 Sekunden. Konkurrierende API-Anfragen erhalten
vorhandene Cache-Zähler; ohne Cache HTTP 503 mit `Retry-After: 30`.
Diese Koordination gilt pro Serverprozess. Es werden keine Netzwerkoperationen
unter der globalen Sperre ausgeführt und laufende Zählungen nicht verdrängt.

Die Zählung nutzt standardmäßig höchstens zwei normale Pool-Verbindungen und
lässt bei Poolgrößen über eins mindestens einen normalen Platz frei. Die
zusätzlichen Plätze für interaktive Lese-/Schreibaktionen bleiben unverändert.
Ein vorhandenes `SELFMAILER_COUNT_WORKERS` wird auf diese Grenze beschränkt.

## Weniger Befehle, gleiche Identitätsprüfung

Die authentifizierten Serverfähigkeiten aus 1.95.4 bleiben maßgeblich.
Bei CONDSTORE-Unterstützung wird direkt `SELECT <sicher kodierter Ordner>
(CONDSTORE)` verwendet, statt vorher ein eigenes `ENABLE` zu senden.
Das entspricht [RFC 7162, Abschnitt 3.1.8](https://www.rfc-editor.org/rfc/rfc7162.html#section-3.1.8).
Eine ausdrücklich abgelehnte erweiterte Auswahl fällt auf normale Auswahl
zurück; der Zusatz wird auf dieser Verbindung nicht wiederholt. Bei einem
Transportfehler wird nicht fortgefahren. MOVE/STORE/DELETE werden nie aufgrund
dieser Behandlung automatisch wiederholt.

Die gültige UIDVALIDITY-Antwort einer gerade erfolgreichen Auswahl wird genau
einmal für denselben Ordner und dieselbe Verbindung geprüft. Sie ist kein
globaler oder kontenübergreifender Cache. Bei erneuter Pool-Ausleihe ohne frisches
SELECT, fehlender oder unbrauchbarer Antwort oder abweichendem Ordner bleibt die
STATUS-Prüfung erhalten. Bei abweichender erwarteter Generation bleibt es bei
HTTP 409 vor jeder UID-Aktion. Eigentumsprüfungen bleiben unverändert.
UIDVALIDITY wird vom Server bereits bei SELECT geliefert; STATUS auf einem
ausgewählten Ordner soll vermieden werden.
[RFC 3501, Abschnitt 6.3.2](https://www.rfc-editor.org/rfc/rfc3501.html#section-6.3.2),
[Abschnitt 6.3.10](https://www.rfc-editor.org/rfc/rfc3501.html#section-6.3.10).

Die Diagnose enthält optional `condstore_mode=select|fallback` und
`generation_source=select|status`. Die kombinierte Auswahl wird vollständig
unter der Phase `select` gemessen, ohne eine separate `condstore`-Runde.
Die Kennungen enthalten keine Ordnernamen, Nachrichtenkennungen oder Inhalte.

## Android-Veröffentlichung

APK-Version **1.95.5**, Build **105**, Paket `com.selfmailer.viewer`.
Diesmal enthält auch die APK einen eigenen Fix. Die Android-Quellen liegen
außerhalb dieses Git-Repositories; ein Patch der Änderungen wird zusammen mit
der signierten APK und deren SHA256 im GitHub-Release bereitgestellt.
Signiermaterial und lokale Konfigurationen werden nicht veröffentlicht.

Vor dem Containerupdate die vorhandene Datensicherung verwenden. Das
GitHub-Release aktualisiert keinen laufenden Unraid-Container automatisch.
Nach dem Update über `latest` und dem APK-Update die Gmail-Laufzeiten erneut
prüfen. Tests verwenden synthetische Nachrichten, keine echten Mailkonten.
