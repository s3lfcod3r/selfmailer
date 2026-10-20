# AGENTS.md — selfmailer

Kurze Orientierung fuer KI-Assistenten (Bonsai/Claude). **Diese Datei zuerst lesen, nicht die grosse README.**
Zum Finden: `rg -n "BEGRIFF"`. Fuer Symbole: Serena `get_symbols_overview`/`find_symbol`. Grosse Dateien nur zeilenweise (`sed -n`).

## Typ & Stack

- Node/JS-TS: `frontend/package.json` (react, vite)
- Python: `backend/requirements.txt`
- Android-App: Quellcode in `_android-src/` (gitignored, nie pushen)

## Bauen / Testen / Starten

```bash
cd frontend && npm run dev   # vite
cd frontend && npm run build   # tsc -b && vite build
cd frontend && npm run test   # node --test tests/*.cjs
uvicorn <modul>:app   # FastAPI-Server (Details: Dockerfile/README)
python -m pytest -q   # falls Tests vorhanden
bash /mnt/user/appdata/android-build/apk-release.sh selfmailer build   # APK bauen
bash /mnt/user/appdata/android-build/apk-release.sh selfmailer         # komplettes Release
```

## Struktur (oberste Ebenen)

- `.github/` — CI/Workflows
- `_android-src/` — Android-Quellcode (lokal)
- `android/` — Android
- `assets/`
- `backend/` — Server (API)
- `docs/` — Dokumentation
- `frontend/` — Web-Oberflaeche
- `unraid/`

## Einstiegspunkte

- `backend/app/main.py`
- `frontend/src/App.tsx`

---
*Automatisch erzeugt als Navigationshilfe. Bei Bedarf von Hand praeziser machen.*
