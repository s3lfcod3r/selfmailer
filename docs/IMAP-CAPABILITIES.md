# SelfMailer 1.95.4: IMAP-Funktionen nach dem Login erkennen

## Behobener Fehler

Die verwendete Kombination aus `imaplib` und `imap_tools` speichert die
Serverfunktionen beim Verbindungsaufbau. Die Anmeldung kann eine andere Liste
liefern; `client.capabilities` wurde bisher danach nicht aktualisiert.
Dadurch konnten native MOVE-Befehle und der sparsamere CONDSTORE-Abgleich
ungenutzt bleiben, obwohl ein Server sie nach der Anmeldung anbietet.

SelfMailer übernimmt jetzt aktuelle CAPABILITY-Antworten aus dem erfolgreichen
Login, bevor CONDSTORE aktiviert und ein Ordner ausgewählt wird. Fehlt eine
verwertbare Antwort, wird CAPABILITY einmal nachgefragt. Unterstützte Funktionen
werden ausschließlich aus der Serverantwort übernommen, nicht anhand des
Hostnamens angenommen. Eine mitgelieferte Login-Antwort spart die Zusatzabfrage.

Dies folgt dem [IMAP-Protokoll, Abschnitt 7.2.1](https://www.rfc-editor.org/rfc/rfc3501.html#section-7.2.1).
Auch Googles [IMAP-Dokumentation](https://developers.google.com/workspace/gmail/imap/imap-extensions)
zeigt die Ermittlung der Funktionen nach der Anmeldung.

## Sicherheit und Kompatibilität

- Server ohne MOVE behalten den bisherigen COPY/STORE/EXPUNGE-Ersatzweg.
- Server ohne CONDSTORE behalten den bisherigen vollständigen Abgleich.
- Ungültige oder abgelehnte Capability-Antworten aktivieren keine alten
  Erweiterungen aus der Vor-Login-Liste.
- Bei Transportfehlern während der Ermittlung wird die Einrichtung abgebrochen
  und die noch nicht dem Pool übergebene Verbindung lokal geschlossen.
- Eigentumsprüfungen und UIDVALIDITY-Prüfungen vor Mailaktionen bleiben bestehen.
- Ein fehlgeschlagenes natives MOVE wird nicht automatisch wiederholt und
  löst keinen zweiten Versuch über COPY/DELETE aus.
- Keine Zugangsdaten, Ordnernamen oder rohen Protokollantworten in den neuen Logs.

Natives MOVE benötigt einen Verschiebebefehl statt separatem COPY, STORE und
EXPUNGE. Es vermeidet dabei den ordnerweiten EXPUNGE-Schritt des Ersatzwegs;
siehe [RFC 6851](https://www.rfc-editor.org/rfc/rfc6851.html#section-3.3).

## Tests und Nachprüfung

Neue Regressionstests verwenden echtes `imaplib` und `imap_tools` mit einem
speicherbasierten Protokolltranskript. Sie prüfen die Login-Antwortvarianten,
gezielte Nachabfrage, entfernte Fähigkeiten, Fehlerfälle, nativen MOVE und
den unveränderten UIDVALIDITY-Schutz. Es werden keine echten Postfächer benötigt.

Nach dem Update des Containers über `latest` muss `/api/health` Version 1.95.4
anzeigen. Mit der [Mail-Zeitmessung](MAIL-TIMING.md) lässt sich bei einer eigenen
Testmail prüfen, ob `move_mode=native` erscheint und die Laufzeit sinkt.
`capability` erscheint als zusätzliche Phase nur bei tatsächlicher Nachabfrage;
`condstore` erfasst den Aktivierungsversuch, nicht den gesamten späteren Sync.

Die Einsparung zusätzlicher IMAP-Befehle ist kein Versprechen einer bestimmten
Ladezeit. Hohe kontobezogene IMAP-Latenzen und belegte Hintergrundverbindungen
können weitere Ursachen haben und müssen nach dem Update erneut gemessen werden.

Dies ist ein Backend-Fix für Web und Android. Die vorhandene Android-APK 1.95.1
bleibt kompatibel; diese Veröffentlichung enthält keine neue APK.
