# SelfMailer 1.95.1

Bugfix- und Sicherheitsupdate für Web und Backend. Die signierte Android-App
trägt zur gemeinsamen Veröffentlichung Version 1.95.1 / Build 104; ihre
Funktionen entsprechen 1.95.0.

## Änderungen

- Notizentwürfe: Verlassen-Warnung, Schutz laufender Schreibvorgänge und
  Erhalt des Textes bei fehlgeschlagenem Speichern. Entwürfe bleiben im
  Arbeitsspeicher; keine Ablage von Notiztexten im Browser-Speicher.
- Notizübersicht: neue Metadaten-API ohne Textvorschauen; vollständige Notizen
  werden erst beim Öffnen geladen. Suche nach Titel oder Inhalt bleibt möglich.
  Beide neuen Routen prüfen Anmeldung und Eigentümer. Die bisherige vollständige
  Listenroute bleibt für vorhandene Android-Clients kompatibel.
- Kalender und zentrale Bestätigungen: native modale Dialoge mit Escape,
  Beschriftungen, Startfokus und Fokusrückgabe. Gleichzeitige Dialoganfragen
  werden nacheinander abgearbeitet.
- OAuth: Client-Secret und Refresh-Token als maskierte Eingabefelder.
- Regeln/Kontaktsuche: veraltete Antworten beim Konto-/Suchwechsel verwerfen.
  Mail-Tastenkürzel nur in der aktiven Mailansicht und außerhalb von Dialogen.
- Web-Konversationen: identische Zusatzabrufe teilen einen laufenden Request.
  Ergebnisse werden für höchstens 15 Sekunden und maximal 32 Einträge im
  Arbeitsspeicher gehalten, gebunden an Konto, Ordner, UID und UIDVALIDITY.
  Nachrichtenänderungen und Kontextwechsel entwerten den Cache. Späte Antworten
  öffnen keinen geschlossenen Thread erneut. Fehlgeschlagenes Löschen stellt
  den unverändert geöffneten Thread korrekt wieder her.
- DOMPurify, Vite und betroffene Build-Abhängigkeiten aktualisiert.

## Update und Grenzen

Web und Backend gemeinsam auf 1.95.1 aktualisieren; vorher die Datenbank sichern.
Die Android-App kann über das Release beziehungsweise SelfStore aktualisiert
werden. Die neuen Web-Notizrouten setzen das neue Backend voraus.

Die Änderungen verringern redundante Web-Thread-Abfragen. Sie ersetzen keinen
Gmail-API-/MIME-Teilabruf und garantieren keine feste Ladezeit. Der erste
IMAP-Abruf und Android-Ladezeiten bleiben vom Anbieter und Netzwerk abhängig.

Notizen sind kein Passworttresor. Metadatenansicht und maskierte Eingabefelder
sind Sichtschutz/Datenminimierung, keine Ende-zu-Ende-Verschlüsselung. HTTPS muss
weiterhin passend zur eigenen Umgebung eingerichtet sein. Eine Verlassen-Warnung
kann bei erzwungenem Browserende oder einem Absturz nicht zuverlässig helfen.

Geprüft: 213 Backendtests, 29 Frontendtests, Ruff, TypeScript und Produktionsbuild;
zusätzlicher Browsertest mit synthetischen Daten einschließlich mobiler Breite.
`npm audit` meldete zum Release-Testzeitpunkt keine bekannten Schwachstellen.
