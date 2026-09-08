# Sicherheits- und Performance-Korrekturen, September 2026

Korrekturpaket für Version 1.94.0 nach der zweiten Code-Prüfung. Backend, Web und
Android müssen zusammen getestet und aktualisiert werden. Die Android-Quellen
liegen außerhalb dieses Repos; die signierte APK wird als Release-Asset angeboten.
Eine Veröffentlichung führt keine manuelle Migration einer produktiven DB aus.

## Mail-Identität und Update-Reihenfolge

IMAP-Nachrichten werden jetzt durch Konto, Ordner, UIDVALIDITY und UID identifiziert.
Die UID allein ist nach dem Neuaufbau eines Ordners nicht mehr dieselbe Nachricht.
Header und Details liefern `uidvalidity`; UID-Aktionen schicken diesen Wert als
Queryparameter, Batch-/Transfer-Aufrufe im JSON mit. IMAP prüft die Generation auf
derselben Verbindung unmittelbar vor dem Zugriff. Eine abweichende oder nicht
verifizierbare erwartete Generation ergibt HTTP 409: Liste synchronisieren und die
Nachricht neu auswählen, den alten Auftrag nicht automatisch wiederholen.

Beim ersten Start ergänzt die additive DB-Migration `cachedmessage.uidvalidity` und
`devicetoken.session_id`. Alte Cache-Zeilen haben Generation 0. Beim nächsten
erfolgreichen Sync werden unbekannte oder abweichende Generationen atomar neu
aufgebaut. Das kann einmalig zusätzlichen IMAP-Verkehr und eine neue Cache-Füllung
über mehrere Sync-Läufe verursachen; die eigentlichen Mails werden nicht gelöscht.
Alte Clients ohne Generation dürfen auf bereits synchronisierten Ordnern keine
UID-basierten Schreibaktionen mehr ausführen. Cachelose Legacy-Pfade bleiben aus
Kompatibilitätsgründen bestehen; ihre Identitätsgarantie ist entsprechend geringer.

Vor einem Rollout: konsistentes DB-Backup erstellen, passenden Web-Build und passende
Android-App bereithalten, in einer Testinstanz prüfen, erst dann Server aktualisieren.
Die Android-App trägt Version 1.94.0 / versionCode 102. Für ein Update muss sie mit
demselben Release-Schlüssel wie die bereits installierte APK signiert sein.

## Vertrauenswürdige Absenderprüfung

`SELFMAILER_TRUSTED_AUTHSERV_IDS` ist standardmäßig leer. Nur wenn der Betreiber die
Vertrauensgrenze tatsächlich kontrolliert, dürfen exakte authserv-IDs kommasepariert
eingetragen werden. Keine pauschalen Domains/Wildcards und keine Werte ungeprüft aus
einer eingegangenen Mail übernehmen. Ein erlaubter Empfangs-MTA muss fremde Header,
die denselben Aussteller behaupten, vor Zustellung entfernen. Bei mehreren Providern
muss dies für alle relevanten Zustellwege gelten. Ist das nicht gesichert, leer lassen.

Nur das erste Ergebnis eines erlaubten Ausstellers wird ausgewertet. DMARC muss
`pass` melden; SPF/DKIM-Erfolg ohne Alignment überstimmt kein DMARC-Fehlschlagen.
Received-SPF oder fremde Authentication-Results begründen keine positive Anzeige.
Alte Cache-Analysen und Analysen unter einer anderen Trust-Konfiguration gelten nicht
weiter. Auch ein DMARC-Erfolg bestätigt eine Domain, nicht die Absenderperson oder
die Sicherheit des Inhalts. Fehlgeschlagene Prüfungen beweisen keinen Kontoeinbruch.

## Push und Logout

Ein FCM-Gerätetoken wird nur der zuletzt registrierten Anmeldung zugeordnet. Die
Android-App erzeugt eine zufällige Sitzungsbindung und prüft sie vor der Anzeige.
Logout entfernt die lokale Bindung sofort, stoppt Hintergrundarbeit und räumt lokale
Benachrichtigungen auf; die Server-Abmeldung läuft zusätzlich best-effort. Auch bei
fehlendem Netz werden alte/zwischengespeicherte FCM-Nachrichten damit verworfen.
Verspätete Abmeldungen mit alter Bindung entfernen keine neuere Registrierung.

FCM-Registrierungen ohne Sitzungsbindung werden nicht mehr beliefert. Nach dem
App-Update Benachrichtigungen erneut aktivieren beziehungsweise neu anmelden und
Test-Push prüfen. Neue-Mail-FCM-Payloads enthalten generischen Text statt Absender,
Betreff oder Kontobezeichnung. Geräte-/Sitzungskennung, Konto-ID, Ordner und UID sowie
Zeitpunkt/Transportmetadaten bleiben bei Google sichtbar. ntfy bleibt ein gesonderter,
bewusst konfigurierbarer Kanal mit bisheriger Vorschau.

Diese lokale Push-Abmeldung ersetzt **keine serverseitige JWT-Revocation**. Das im
ersten Review festgestellte allgemeine Sitzungsproblem ist separat zu beheben.

## Transfers, Kalender und Migration

- Leere UID-Auswahl beim Transfer ist ein No-op; nur `uids: null` bezeichnet einen
  ganzen Ordnerbaum. Gleiche Message-ID im Ziel ist kein Löschbeleg. Die Quelle bleibt
  in diesem Fall erhalten und die Antwort meldet dies. Nur bestätigte APPENDs dürfen
  im aktuellen Durchlauf anschließend gelöscht werden. Bei Netzwerk-Timeouts bleibt
  eine manuelle Kontrolle möglicher Doppelkopien notwendig.
- Kontoübergreifender Google-Kalenderwechsel legt zuerst den Zieltermin an. Ohne
  bestätigte Ziel-ID wird die Quelle nicht gelöscht. Scheitert danach das Löschen,
  wird ein Teilfehler gemeldet. Beide Kalender prüfen, nicht blind wiederholen; das
  ist keine verteilte atomare Transaktion oder automatische Wiederherstellung.
- Das Migrationslimit zählt noch nicht kopierte Nachrichten. Wiederholte Läufe
  kommen über die ersten N Nachrichten hinaus. `remaining` und `complete` machen
  den Rest sichtbar. Message-ID-basierte Duplikaterkennung bleibt eine Heuristik;
  fehlende oder kollidierende Message-IDs erfordern bei Bedarf manuelle Kontrolle.
- Push-Vorschauen laden maximal eine Kopfzeile ohne Mail-Body. Unveränderte DAV-Termine
  werden nicht mehr alle erneut geschrieben. Geänderte Termine werden weiter aktualisiert.
- TOTP und Backup-Codes werden mittels bedingtem SQL-UPDATE atomar verbraucht.

## Tests und Grenzen

`backend/tests/test_review_regressions.py` verwendet synthetische SQLite-Datenbanken
und IMAP-/Google-/FCM-Testdoubles. Die 2FA-Racetests prüfen echte TOTP-/Argon2-Verifikation
in zwei gleichzeitigen DB-Sessions. SQL-Events messen tatsächlich geladene Mail-Zeilen
und Kalender-UPDATEs. Keine echten Postfächer werden angesprochen.

`cd frontend && npm test` prüft die tatsächlichen TypeScript-Funktionskörper mit
gesteuerten Promise-Antworten: Kontowechsel, Pagination, Filter, Löschen und Thread-UIDs.
Das ergänzt, ersetzt aber keine Browser-/Geräte-End-to-End-Tests. `npm run build`
prüft TypeScript und den Produktionsbuild. Android mindestens mit
`:app:compileDebugKotlin` prüfen; reales Logout/Re-Login, Offline-Push und biometrische
Deep-Links zusätzlich auf einem Testgerät testen.

Die zweite Prüfung ist kein vollständiger Sicherheitsnachweis. Insbesondere die
offenen Punkte des ersten Reviews (Benutzer-/Kontolöschung und verwaiste Datensätze,
JWT-Widerruf, DAV-Zielvertrauen, Zugriff auf Pool-Diagnosen, weitere Last-/Abhängigkeits-
themen) sind durch dieses Änderungspaket nicht insgesamt erledigt.
