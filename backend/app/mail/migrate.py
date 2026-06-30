"""Postfach-Migration (Konto → Konto, mit Ordnerstruktur).

Kopiert ein komplettes Quellkonto (alle Ordner + Unterordner inkl. Mails) in
ein Zielkonto und erhält die Struktur:

- Quelle und Ziel sind IMAP-Konten (Synology MailPlus muss als IMAP-Konto
  eingebunden sein, nicht POP3).
- Ordnerpfade werden auf das Trennzeichen des ZIELservers übersetzt; optional
  landet alles unter einem Ziel-Elternordner (``target_prefix``), damit die
  alten Daten getrennt vom Bestand liegen.
- Dedup über Message-ID: bereits im Zielordner vorhandene Mails werden
  übersprungen → erneutes Ausführen erzeugt keine Duplikate (idempotent).
- ``dry_run=True`` zählt nur pro Ordner und schreibt nichts (Vorschau).
"""
from __future__ import annotations

import datetime as _dt

from imap_tools import AND, MailBox

from ..models import MailAccount
from .imap import _IMAP_TIMEOUT, _delimiter

SEEN = r"\Seen"


def _aware(d: _dt.datetime | None) -> _dt.datetime | None:
    """IMAP APPEND verlangt eine zeitzonenbehaftete Zeit. Mails mit Datum ohne
    Zeitzone (naiv) bekommen UTC, sonst wirft imap-tools 'date_time must be aware'."""
    if d is not None and d.tzinfo is None:
        return d.replace(tzinfo=_dt.timezone.utc)
    return d


def _open(acc: MailAccount, password: str, folder: str = "INBOX") -> MailBox:
    # Timeout wie in imap._connect: ein toter/hängender Server darf den
    # Migrations-/Transfer-Worker nicht UNENDLICH blockieren.
    try:
        box = MailBox(acc.imap_host, port=acc.imap_port, timeout=_IMAP_TIMEOUT)
    except TypeError:  # ältere imap_tools-Version ohne timeout-Param
        box = MailBox(acc.imap_host, port=acc.imap_port)
    box.login(acc.auth_user or acc.email, password, initial_folder=folder)
    try:
        box.client.sock.settimeout(_IMAP_TIMEOUT)
    except Exception:  # noqa: BLE001 - best effort, falls Socket anders heißt
        pass
    return box


def _dest_path(source_folder: str, src_delim: str, dst_delim: str, prefix: str) -> str:
    """Quellpfad auf das Ziel-Trennzeichen übersetzen, optional unter prefix."""
    segs = [s for s in source_folder.split(src_delim) if s]
    if prefix:
        segs = [p for p in prefix.split(dst_delim) if p] + segs
    return dst_delim.join(segs)


def _ensure_folder(dst: MailBox, path: str, delim: str) -> bool:
    """Legt den Pfad (inkl. fehlender Elternebenen) im Ziel an. Prüft per
    exists(), um doppelte Anlage zu vermeiden, und liefert zurück, ob der Ordner
    am Ende existiert (sonst kann/soll man ihn überspringen)."""
    cur = ""
    for part in [p for p in path.split(delim) if p]:
        cur = f"{cur}{delim}{part}" if cur else part
        try:
            if not dst.folder.exists(cur):
                dst.folder.create(cur)
        except Exception:  # noqa: BLE001 - existiert bereits / Server lehnt ab
            pass
    try:
        return dst.folder.exists(path)
    except Exception:  # noqa: BLE001 - im Zweifel weitermachen
        return True


def _existing_message_ids(dst: MailBox, folder: str) -> set[str]:
    """Message-IDs, die im Zielordner schon liegen (für Dedup)."""
    ids: set[str] = set()
    try:
        dst.folder.set(folder)
        for msg in dst.fetch(AND(all=True), mark_seen=False, headers_only=True, bulk=50):
            mid = (msg.headers.get("message-id", ("",))[0] or "").strip()
            if mid:
                ids.add(mid)
    except Exception:  # noqa: BLE001 - Ordner evtl. neu/leer
        pass
    return ids


def migrate_folders(
    source: MailAccount,
    source_pw: str,
    dest: MailAccount,
    dest_pw: str,
    *,
    target_prefix: str = "",
    dry_run: bool = True,
    limit_per_folder: int = 5000,
) -> dict:
    """Kopiert alle Ordner des Quellkontos ins Zielkonto (struktur-erhaltend).

    Liefert {folders:[{source,dest,count,copied,skipped}], errors, dry_run}.
    """
    folders_out: list[dict] = []
    errors: list[str] = []
    src = _open(source, source_pw)
    dst = None if dry_run else _open(dest, dest_pw)
    try:
        src_delim = _delimiter(src)
        dst_delim = _delimiter(dst) if dst else src_delim
        names = [f.name for f in src.folder.list() if f.name]
        names.sort(key=lambda n: (n.count(src_delim), n.lower()))  # Eltern vor Kindern
        for sf in names:
            df = _dest_path(sf, src_delim, dst_delim, target_prefix)
            try:
                src.folder.set(sf)
                total = len(src.uids())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{sf}: {type(exc).__name__}")
                continue
            entry = {"source": sf, "dest": df, "count": total, "copied": 0, "skipped": 0}
            if not dry_run and total:
                _ensure_folder(dst, df, dst_delim)
                seen_ids = _existing_message_ids(dst, df)
                src.folder.set(sf)
                copied = skipped = 0
                for msg in src.fetch(AND(all=True), limit=limit_per_folder, mark_seen=False, bulk=50):
                    mid = (msg.headers.get("message-id", ("",))[0] or "").strip()
                    if mid and mid in seen_ids:
                        skipped += 1
                        continue
                    try:
                        flags = [SEEN] if SEEN in (msg.flags or ()) else None
                        dst.append(msg.obj.as_bytes(), df, dt=_aware(msg.date), flag_set=flags)
                        copied += 1
                        if mid:
                            seen_ids.add(mid)
                    except Exception as exc:  # noqa: BLE001 - einzelne Mail darf scheitern
                        if len(errors) < 8:
                            errors.append(f"{df}: {type(exc).__name__}: {exc}")
                entry["copied"] = copied
                entry["skipped"] = skipped
            folders_out.append(entry)
    finally:
        for box in (src, dst):
            if box is not None:
                try:
                    box.logout()
                except Exception:  # pragma: no cover - best effort
                    pass

    return {"folders": folders_out, "errors": errors, "dry_run": dry_run}


def transfer_messages(
    source: MailAccount,
    source_pw: str,
    source_folder: str,
    uids: list[str] | None,
    dest: MailAccount,
    dest_pw: str,
    dest_folder: str,
    *,
    move: bool = False,
    limit: int = 2000,
) -> dict:
    """Kopiert/verschiebt einzelne Mails (uids) oder einen ganzen Ordner
    (uids=None) — INKL. aller Unterordner — aus dem Quell- in ein ANDERES Konto.

    Bei uids=None wird der komplette Teilbaum unter source_folder übertragen und
    die Ordnerstruktur unter dest_folder nachgebaut. move=True löscht die
    Quellmails nach erfolgreicher Ablage (Ordner bleiben leer stehen). Dedup per
    Message-ID verhindert Duplikate. Liefert {copied, skipped, deleted, errors}.
    """
    copied = skipped = deleted = 0
    errors: list[str] = []
    src = _open(source, source_pw, source_folder)
    dst = _open(dest, dest_pw)
    try:
        src_delim = _delimiter(src)
        dst_delim = _delimiter(dst)
        if uids:
            pairs: list[tuple[str, str, list[str] | None]] = [(source_folder, dest_folder, uids)]
        else:
            # Ganzer Ordner + alle Unterordner: Teilbaum sammeln, Struktur erhalten.
            names = [f.name for f in src.folder.list() if f.name]
            subtree = [n for n in names if n == source_folder or n.startswith(source_folder + src_delim)]
            subtree.sort(key=lambda n: n.count(src_delim))  # Eltern vor Kindern
            base = [p for p in dest_folder.split(dst_delim) if p]
            pairs = []
            for n in subtree:
                rel_segs = [s for s in n[len(source_folder):].split(src_delim) if s]
                pairs.append((n, dst_delim.join(base + rel_segs), None))

        aborted = False
        for sf, df, fuids in pairs:
            if not _ensure_folder(dst, df, dst_delim):
                # Echte Ursache einfangen (z. B. "quota exceeded" = Zielpostfach voll).
                reason = "konnte nicht angelegt werden"
                try:
                    dst.folder.create(df)
                except Exception as exc:  # noqa: BLE001
                    reason = str(exc).split("Data:")[-1].strip() or reason
                # Bei vollem Zielpostfach hat Weitermachen keinen Sinn → sofort sauber
                # abbrechen (sonst Fehler-Flut bis "Broken pipe").
                if "quota" in reason.lower() or "voll" in reason.lower():
                    errors.append("Zielpostfach VOLL (Quota überschritten) — Übertragung abgebrochen. Bitte erst Speicher freigeben.")
                    break
                if len(errors) < 8:
                    errors.append(f"{df}: {reason}")
                continue
            seen_ids = _existing_message_ids(dst, df)
            src.folder.set(sf)
            crit = AND(uid=",".join(fuids)) if fuids else AND(all=True)
            done: list[str] = []
            for msg in src.fetch(crit, limit=limit, mark_seen=False, bulk=50):
                mid = (msg.headers.get("message-id", ("",))[0] or "").strip()
                if mid and mid in seen_ids:
                    skipped += 1
                    if msg.uid:
                        done.append(msg.uid)  # schon im Ziel → bei move trotzdem aus Quelle weg
                    continue
                try:
                    flags = [SEEN] if SEEN in (msg.flags or ()) else None
                    dst.append(msg.obj.as_bytes(), df, dt=_aware(msg.date), flag_set=flags)
                    copied += 1
                    if mid:
                        seen_ids.add(mid)
                    if msg.uid:
                        done.append(msg.uid)
                except Exception as exc:  # noqa: BLE001
                    if "quota" in str(exc).lower():
                        errors.append("Zielpostfach VOLL (Quota überschritten) — Übertragung abgebrochen. Bitte erst Speicher freigeben.")
                        aborted = True
                        break
                    if len(errors) < 8:
                        errors.append(f"{df}: {type(exc).__name__}: {exc}")
            if aborted:
                break
            if move and done:
                src.folder.set(sf)
                try:
                    src.delete(done)
                    deleted += len(done)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"delete {sf}: {type(exc).__name__}: {exc}")
    finally:
        for box in (src, dst):
            try:
                box.logout()
            except Exception:  # pragma: no cover - best effort
                pass

    return {"copied": copied, "skipped": skipped, "deleted": deleted, "errors": errors}
