# Gmail und langsame IMAP-Konten: 1.95.0

Backend/Web und Android gemeinsam aktualisieren. Android: versionCode 103,
Paket `com.selfmailer.viewer`. Die Sicherheitskorrekturen aus 1.94.0 bleiben aktiv.

## Änderungen

- Die Ordnersperre respektiert das Zeitbudget des interaktiven Syncs; ihr Warten
  wird vom verbleibenden Verbindungsbudget abgezogen und als `ms.ordnersperre`
  mitgemessen. Eine bereits laufende Synchronisierung löst keinen weiteren
  teuren Live-Listenabruf als Fehler-Fallback aus.
- Bestehende Zusatzverbindungen bleiben für interaktive Aktionen verfügbar.
  Hintergrund-Sync, Zähler, Vorladen, Thread-Ergänzung, Export und Aufräumen
  bleiben im regulären Kontingent. Der Gesamtpool wurde nicht vergrößert.
- Listen, Suche und Thread-Ergänzung holen Kopfzeilen, Flags, Größe und
  BODYSTRUCTURE statt kompletter Mails einschließlich Anhängen. Anhangsymbole
  werden aus MIME-Metadaten ermittelt. Ist deren Antwort ungültig, wird nur der
  Header geladen; das Symbol kann dann erst nach Öffnen der Mail stimmen.
- Kopfzeilen werden pro Paket von höchstens 50 UIDs mit einem FETCH geladen.
  Bekannte UIDs brauchen keine zusätzliche SEARCH-Abfrage. Syncs stoppen
  zwischen Paketen nach einem Zwei-Sekunden-Abrufbudget und setzen später fort.
  Dies ist kein harter Zwei-Sekunden-Timeout für ein bereits laufendes Kommando.
- Vorladen: maximal drei Kandidaten, nur Nachrichten bis 64 KiB, keine UI-Reserve,
  kein Warten auf einen freien Pool-Platz. Je Konto/Ordner nur eine laufende
  Vorladung, insgesamt höchstens zwei Vorladeanfragen pro Prozess.
- Einzellöschen in Web/Android reagiert sofort mit einem sichtbaren laufenden
  Zustand. Fehler werden angezeigt und die Darstellung zurückgenommen, ohne
  fremde Konten oder wiederverwendete UIDs zu überschreiben. Kurzlebige,
  generationsgebundene Löschmarker verhindern Wiedererscheinen durch verspätete
  Listen-/Thread-Antworten. Erst eine Serverbestätigung gilt als Erfolg.
- Bereits als leer synchronisierte Ordner lösen keine wiederholten Live-Fallbacks aus.

## Sichtbare Unterschiede und Grenzen

Vorschauen neuer Mails können zunächst leer sein; sie entstehen beim begrenzten
Vorladen oder Öffnen. Große Einzelmails werden beim expliziten Öffnen weiterhin
vollständig abgerufen. Dieses Update baut noch keinen MIME-Teilabruf für große
Einzelmails oder einen Gmail-API-Connector ein. Ein voller Nachrichtenabruf kann
also weiterhin durch große Anhänge oder Provider-Latenz langsam sein.

Der erste Cache-Aufbau und die Serverbestätigung eines Löschens bleiben abhängig
von Gmail und Netzwerk. Keine pauschale Sekundenersparnis zugesichert. Die Tests
verwenden simulierte Serverantworten und temporäre Datenbanken; kein produktives
Postfach wurde gelesen oder verändert, kein physisches Android-Gerät getestet.

Die UIDVALIDITY-Prüfung bleibt auch vor Löschen/Verschieben bestehen. Keine
Abkürzung durch Entfernen von Sicherheitsprüfungen, kein automatisches Wiederholen
einer unklar bestätigten destruktiven Mail-Aktion.

## Nach dem Update prüfen

1. `/api/health` auf Version 1.95.0 prüfen.
2. Dieselbe Gmail-Liste kalt und warm öffnen; eine kleine und eine große Mail lesen.
3. Eine entbehrliche Testmail normal löschen und ihre Ankunft im Papierkorb prüfen.
4. Parallel Web und Android öffnen; bei einem simulierten Verbindungsfehler muss
   eine fehlgeschlagene Löschung sichtbar bleiben und darf keinen Erfolg vortäuschen.
5. Sync-Antworten/Logs vergleichen: Ordnersperre, Verbindungswartezeit, IMAP-Zeit.

Eine APK-Aktualisierung über SelfStore aktualisiert nicht automatisch den selbst
gehosteten Backend-Container. Vor dessen Aktualisierung die Datenbank sichern.
