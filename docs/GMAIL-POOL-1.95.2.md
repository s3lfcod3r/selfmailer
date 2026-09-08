# SelfMailer 1.95.2: IMAP-Pool-Korrekturen

Server-/Container-Update für Gmail und andere IMAP-Konten. Die bestehende
Android-App 1.95.1 / Build 104 bleibt kompatibel; dieses Release enthält keine
neue APK und verlangt kein App-Update. SelfStore behält die vorhandene APK.

## Korrekturen

- Fertige Mail-Aktionen warten nicht mehr auf das globale Abmelden ungenutzter
  Verbindungen. Höchstens ein Hintergrund-Aufräumer pro Prozess, keine
  Warteschlange und frühestens alle 30 Sekunden ein neuer Durchlauf. Ein noch
  laufender Durchlauf verhindert weitere Worker, auch nach Ablauf des Intervalls.
- Das Aufräumen entfernt nur die tatsächlich geschlossene Verbindung, statt
  die aktuelle Verbindungsliste mit einem alten Snapshot zu überschreiben.
  Gleichzeitig neu angelegte Verbindungen bleiben dadurch erhalten.
- Eine Verbindung zählt bis zum Ende des Abmeldens weiter zum Kontingent.
  Aktive und inzwischen wieder frisch benutzte Verbindungen bleiben unberührt.
  Netzwerk-Abmelden hält nicht die globale Pool-Sperre.
- Aufräumfehler und Fehler beim Worker-Start werden ohne Provider-Antworten
  oder Zugangsdaten gemeldet und verhindern spätere Versuche nicht dauerhaft.

Verbindungslimits und UIDVALIDITY-Schutz bleiben unverändert. Keine automatische
Wiederholung von Löschungen, keine Änderung am Mail-Cache oder an der API.

## Prüfungen und Grenzen

Die beiden ursprünglichen Fehler wurden mit gezielten Regressionstests zunächst
gegen den alten Code reproduziert. Zwölf neue Tests decken außerdem echte
gleichzeitige Zugriffe, Altersprüfung, Kontingente, Worker-Drosselung und
Fehlerpfade ab. Gesamte Backend-Suite: 225 Tests bestanden; Ruff erfolgreich.

Die Tests verwenden synthetische Verbindungen und isolierte Testdatenbanken.
Sie garantieren keine bestimmte Gmail-Laufzeit. Weitere Wartezeit kann beim
Verbindungsaufbau oder bei eigentlichen IMAP-Kommandos entstehen. Ein erneuter
Gmail-Livetest ist erst nach Aktualisierung des laufenden Servers aussagekräftig.

## Aktualisierung

Vorher die Datenbank über das bestehende Sicherungsverfahren sichern. Danach
den Container auf `ghcr.io/s3lfcod3r/selfmailer:1.95.2` aktualisieren und prüfen,
dass `/api/health` Version `1.95.2` anzeigt. Der GitHub-Release beziehungsweise
Image-Push allein aktualisiert keinen laufenden Container.

Für den Nachtest kleine, klar markierte eigene Nachrichten verwenden: Versand,
erstes und erneutes Öffnen, normale Löschung in den Papierkorb. Erfolg sowohl
an der Serverbestätigung als auch am nachgewiesenen Zielordner prüfen. Keine
fremden Mails löschen oder Papierkörbe leeren.
