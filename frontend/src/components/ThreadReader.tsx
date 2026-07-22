import { useEffect, useRef, useState } from "react";
import { api, download, type MsgHeader, type MsgDetail } from "../lib/api";
import { useLang } from "../lib/i18n";
import { parseAddr, prettyDate, listDate, hasRemoteContent, buildSrcDoc, fmtSize } from "../lib/mailview";
import type { Conversation } from "../lib/threads";

/**
 * Lesebereich für eine Konversation (mehrere zusammengehörende Mails).
 *
 * Darstellung wie Synology MailPlus: die Nachrichten stehen chronologisch
 * gestapelt, jede als eigene aufklappbare Karte. Die neueste (und ungelesene)
 * sind offen, ältere eingeklappt — so ist der Verlauf klar getrennt, statt als
 * ein langer zitierter Textblock zu verschwimmen.
 *
 * Die Bodies werden pro Nachricht LAZY geladen (erst beim Aufklappen) und
 * zwischengespeichert — ein bereits geöffneter Verlauf kostet kein erneutes IMAP.
 */
export function ThreadReader({
  accountId, folder, conversation, blockImages, darkMail,
  onClose, onReply, onForward, onDelete, onFlag, onSeen,
}: {
  accountId: number;
  folder: string;
  conversation: Conversation;
  blockImages: boolean;
  darkMail: boolean;
  onClose: () => void;
  onReply: (d: MsgDetail) => void;
  onForward: (d: MsgDetail) => void;
  onDelete: (m: MsgHeader) => void;
  onFlag: (m: MsgHeader) => void;
  /** Meldet dem Elternteil: diese (vorher ungelesene) Mail wurde geöffnet. */
  onSeen: (m: MsgHeader) => void;
}) {
  const { t } = useLang();
  const msgs = conversation.messages; // chronologisch aufsteigend
  const latestUid = conversation.latest.uid;

  // Geladene Detail-Bodies je UID (Cache über die Lebensdauer der Ansicht).
  const [details, setDetails] = useState<Record<string, MsgDetail>>({});
  const [loadingUid, setLoadingUid] = useState<Set<string>>(new Set());
  const [errUid, setErrUid] = useState<Record<string, string>>({});
  // Aufgeklappte Nachrichten. Start: neueste + alle ungelesenen.
  const [openUids, setOpenUids] = useState<Set<string>>(() => {
    const s = new Set<string>();
    s.add(latestUid);
    for (const m of msgs) if (!m.seen) s.add(m.uid);
    return s;
  });
  // Pro Nachricht: externe Bilder freigegeben?
  const [imgOk, setImgOk] = useState<Set<string>>(new Set());
  // Verhindert doppeltes „als gelesen melden" pro UID in dieser Ansicht.
  const seenSent = useRef<Set<string>>(new Set());

  const msgFolder = (m: MsgHeader) => m.folder || folder;

  async function loadDetail(m: MsgHeader) {
    const uid = m.uid;
    if (details[uid] || loadingUid.has(uid)) return;
    setLoadingUid((s) => new Set(s).add(uid));
    try {
      const d = await api.get<MsgDetail>(
        `/mail/${accountId}/messages/${uid}?folder=${encodeURIComponent(msgFolder(m))}`,
      );
      setDetails((prev) => ({ ...prev, [uid]: d }));
      setErrUid((prev) => { const n = { ...prev }; delete n[uid]; return n; });
      if (!m.seen && !seenSent.current.has(uid)) {
        seenSent.current.add(uid);
        onSeen(m);
      }
    } catch (e) {
      setErrUid((prev) => ({ ...prev, [uid]: (e as Error).message || "Fehler" }));
    } finally {
      setLoadingUid((s) => { const n = new Set(s); n.delete(uid); return n; });
    }
  }

  // Beim Öffnen: Bodies der initial aufgeklappten Nachrichten laden.
  // Bewusst nur bei Konversationswechsel (key), nicht bei jedem Render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    for (const m of msgs) if (openUids.has(m.uid)) loadDetail(m);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation.key]);

  function toggle(m: MsgHeader) {
    setOpenUids((prev) => {
      const n = new Set(prev);
      if (n.has(m.uid)) n.delete(m.uid);
      else { n.add(m.uid); loadDetail(m); }
      return n;
    });
  }

  return (
    <div className="mail-readcol thread-readcol">
      <div className="thread-head">
        <h2 className="mail-head-subject">{conversation.subject || t("mail.noSubject")}</h2>
        <div className="thread-head-actions">
          <span className="thread-count" title={t("shell.conversationView")}>💬 {conversation.count}</span>
          <button className="icon-btn" onClick={onClose} title={t("mail.back")}>✕</button>
        </div>
      </div>
      <hr style={{ borderColor: "var(--self-line)", margin: "0.4rem 0 0.6rem" }} />

      <div className="thread-list">
        {msgs.map((m) => {
          const isOpen = openUids.has(m.uid);
          const d = details[m.uid];
          const from = parseAddr(m.from);
          const sameAddr = from.name.trim().toLowerCase() === from.email.trim().toLowerCase();
          const dark = darkMail;
          const showImgs = imgOk.has(m.uid);
          const remote = !!d?.html && hasRemoteContent(d.html);
          return (
            <div key={`${m.folder ?? ""}:${m.uid}`} className={`thread-msg ${isOpen ? "open" : ""} ${m.seen ? "" : "unseen"}`}>
              <div className="thread-msg-head" role="button" tabIndex={0}
                onClick={() => toggle(m)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(m); } }}>
                <button className="thread-star" onClick={(e) => { e.stopPropagation(); onFlag(m); }} title={t("mail.flag")}>
                  {m.flagged ? "★" : "☆"}
                </button>
                <div className="thread-msg-who">
                  <span className="thread-msg-name">{from.name}</span>
                  {!sameAddr && <span className="thread-msg-addr">&lt;{from.email}&gt;</span>}
                  {!isOpen && m.snippet && <span className="thread-msg-snip">{m.snippet}</span>}
                </div>
                {m.has_attachments && <span className="thread-clip" title={t("mail.attachments")}>📎</span>}
                <span className="thread-msg-date">{isOpen ? prettyDate(m.date) : listDate(m.date)}</span>
                <span className="thread-chevron" aria-hidden>{isOpen ? "▴" : "▾"}</span>
              </div>

              {isOpen && (
                <div className="thread-msg-body">
                  {loadingUid.has(m.uid) && !d && (
                    <div className="mail-loading" style={{ minHeight: 80 }}><span className="mail-spinner" aria-hidden /></div>
                  )}
                  {errUid[m.uid] && <div className="err">{errUid[m.uid]}</div>}
                  {d && (
                    <>
                      <div className="thread-msg-toolbar">
                        <button className="ghost" onClick={() => onReply(d)} title={t("mail.reply")}>↩ {t("mail.reply")}</button>
                        <button className="ghost" onClick={() => onForward(d)} title={t("mail.forward")}>↪ {t("mail.forward")}</button>
                        {blockImages && !showImgs && remote && (
                          <button className="ghost" onClick={() => setImgOk((s) => new Set(s).add(m.uid))} title={t("mail.showImages")}>🖼 {t("mail.showImages")}</button>
                        )}
                        <span className="grow" />
                        <button className="ghost read-del" onClick={() => onDelete(m)} title={t("mail.delete")}>🗑</button>
                      </div>
                      {d.html ? (
                        <iframe title={`mail-${m.uid}`} sandbox="allow-popups allow-popups-to-escape-sandbox"
                          className="mail-body-frame thread-body-frame"
                          srcDoc={buildSrcDoc(d.html, blockImages && !showImgs, dark)} />
                      ) : d.text ? (
                        <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{d.text}</div>
                      ) : (
                        <div className="muted" style={{ whiteSpace: "pre-wrap" }}>{t("mail.emptyBody")}</div>
                      )}
                      {d.attachments?.length > 0 && (
                        <div className="thread-atts">
                          <div className="label" style={{ marginBottom: "0.4rem" }}>📎 {t("mail.attachments")}</div>
                          <div className="row" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
                            {d.attachments.map((att) => (
                              <button key={att.index} className="ghost" style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}
                                onClick={() => download(`/mail/${accountId}/messages/${m.uid}/attachments/${att.index}?folder=${encodeURIComponent(msgFolder(m))}`).catch(() => {})}>
                                <span style={{ overflow: "hidden", textOverflow: "ellipsis", maxWidth: 220, whiteSpace: "nowrap" }}>⬇ {att.filename}</span>
                                <span className="muted" style={{ fontSize: "0.72rem" }}>{fmtSize(att.size)}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
