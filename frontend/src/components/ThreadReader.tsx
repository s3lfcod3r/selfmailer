import { useEffect, useRef, useState } from "react";
import { api, download, type MsgHeader, type MsgDetail, type MailLabel } from "../lib/api";
import { useLang } from "../lib/i18n";
import { parseAddr, prettyDate, listDate, hasRemoteContent, buildSrcDoc, fmtSize, trimQuotedHtml, trimQuotedText, avatarFor } from "../lib/mailview";
import type { Conversation } from "../lib/threads";

// Volle Aktionen je Thread-Nachricht (wie in der Einzelansicht).
export type ThreadActions = {
  labels: MailLabel[];
  labelMap: Record<string, MailLabel>;
  onLabel: (m: MsgHeader, keyword: string, on: boolean) => void;
  onNewLabel: () => void;
  onSpam?: (m: MsgHeader) => void;
  onMarkUnread: (m: MsgHeader) => void;
  onBlock: (m: MsgHeader) => void;
  onAddContact: (m: MsgHeader) => void;
  onMove: (m: MsgHeader, dest: string) => void;
  folders: string[];
  onViewSource: (m: MsgHeader) => void;
};

// Kleiner Absender-Avatar: Kontakt-Foto, sonst Initialen + Farbe.
function Avatar({ label, photo, size = 30 }: { label: string; photo?: string; size?: number }) {
  if (photo) {
    return <img className="thread-avatar" src={photo} alt="" style={{ width: size, height: size, objectFit: "cover" }} />;
  }
  const { initials, color } = avatarFor(label);
  return (
    <span className="thread-avatar" aria-hidden
      style={{ width: size, height: size, background: color, fontSize: size * 0.4 }}>
      {initials}
    </span>
  );
}

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
  accountId, folder, conversation, blockImages, darkMail, ownEmails = [], meLabel = "", avatarMap = {}, actions,
  onClose, onReply, onForward, onDelete, onFlag, onSeen,
}: {
  accountId: number;
  folder: string;
  conversation: Conversation;
  blockImages: boolean;
  darkMail: boolean;
  /** Eigene Absenderadressen → im Verlauf als „Ich" statt der Adresse. */
  ownEmails?: string[];
  meLabel?: string;
  /** E-Mail→Foto für Kontakt-Avatare. */
  avatarMap?: Record<string, string>;
  /** Volle Aktionen je Nachricht (Label/Spam/⋯-Menü). */
  actions?: ThreadActions;
  onClose: () => void;
  onReply: (d: MsgDetail) => void;
  onForward: (d: MsgDetail) => void;
  onDelete: (m: MsgHeader) => void;
  onFlag: (m: MsgHeader) => void;
  /** Meldet dem Elternteil: diese (vorher ungelesene) Mail wurde geöffnet. */
  onSeen: (m: MsgHeader) => void;
}) {
  const { t, lang } = useLang();
  const msgs = conversation.messages; // chronologisch aufsteigend
  const latestUid = conversation.latest.uid;
  const ownSet = new Set(ownEmails.map((e) => e.trim().toLowerCase()).filter(Boolean));
  // Anzeigename eines Absenders — eigene Adresse als „Ich".
  const nameOf = (m: MsgHeader) => {
    const a = parseAddr(m.from);
    if (meLabel && a.email && ownSet.has(a.email.trim().toLowerCase())) return meLabel;
    return a.name || a.email;
  };

  // Geladene Detail-Bodies je UID (Cache über die Lebensdauer der Ansicht).
  const [details, setDetails] = useState<Record<string, MsgDetail>>({});
  const [loadingUid, setLoadingUid] = useState<Set<string>>(new Set());
  const [errUid, setErrUid] = useState<Record<string, string>>({});
  // Offene Menüs je Karte (Schlüssel = folder:uid): Label-Menü bzw. ⋯-Menü.
  const [lblMenuKey, setLblMenuKey] = useState<string | null>(null);
  const [moreMenuKey, setMoreMenuKey] = useState<string | null>(null);
  const keyFor = (m: MsgHeader) => `${m.folder ?? ""}:${m.uid}`;
  // Aufgeklappte Nachrichten. Start: neueste + alle ungelesenen.
  const [openUids, setOpenUids] = useState<Set<string>>(() => {
    const s = new Set<string>();
    s.add(latestUid);
    for (const m of msgs) if (!m.seen) s.add(m.uid);
    return s;
  });
  // Pro Nachricht: externe Bilder freigegeben?
  const [imgOk, setImgOk] = useState<Set<string>>(new Set());
  // Pro Nachricht: zitierten Verlauf ("Am … schrieb:") eingeblendet?
  const [quoteOk, setQuoteOk] = useState<Set<string>>(new Set());
  // Gemessene Höhe des Mail-iframes je UID → volle Anzeige OHNE inneren Scrollbalken.
  const [heights, setHeights] = useState<Record<string, number>>({});
  // Verhindert doppeltes „als gelesen melden" pro UID in dieser Ansicht.
  const seenSent = useRef<Set<string>>(new Set());
  // iframe-Elemente je UID (zum Nachmessen bei Größenänderung des Fensters).
  const frameRefs = useRef<Map<string, HTMLIFrameElement>>(new Map());

  const msgFolder = (m: MsgHeader) => m.folder || folder;

  // Höhe eines Mail-iframes an seinen Inhalt anpassen. Der iframe ist per
  // sandbox="… allow-same-origin" (aber OHNE allow-scripts) lesbar, ohne dass die
  // Mail Code ausführen darf → wir dürfen scrollHeight messen. Gedeckelt gegen
  // Ausreißer.
  function measure(uid: string, el: HTMLIFrameElement | null) {
    if (!el) return;
    try {
      const doc = el.contentDocument;
      if (!doc || !doc.body) return;
      // "Kollabieren-dann-messen": iframe kurz auf 0 setzen, damit
      // documentElement.scrollHeight die ECHTE Inhaltshöhe liefert (inkl.
      // Body-Ränder) statt der aktuellen Viewporthöhe. Sofort im selben Frame
      // wieder setzen → kein sichtbares Flackern. Ohne den Trick blieb sonst ein
      // ~20px-Überhang (Body-Rand) und damit ein dünner innerer Scrollbalken.
      el.style.height = "0px";
      const h = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight);
      const clamped = Math.min(8000, Math.max(48, h + 4));
      el.style.height = `${clamped}px`;
      setHeights((prev) => (prev[uid] === clamped ? prev : { ...prev, [uid]: clamped }));
    } catch { /* cross-origin o. Ä. → feste Fallback-Höhe bleibt */ }
  }

  // Bei Fensterbreiten-Änderung reflowen die Mails → Höhen neu messen.
  useEffect(() => {
    let raf = 0;
    const onResize = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => frameRefs.current.forEach((el, uid) => measure(uid, el)));
    };
    window.addEventListener("resize", onResize);
    return () => { window.removeEventListener("resize", onResize); cancelAnimationFrame(raf); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2 className="mail-head-subject">{conversation.subject || t("mail.noSubject")}</h2>
          {conversation.fromNames.length > 0 && (
            <div className="thread-participants" title={conversation.fromNames.join(", ")}>
              {conversation.fromNames.join(", ")}
            </div>
          )}
        </div>
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
          const dispName = nameOf(m);
          const sameAddr = from.name.trim().toLowerCase() === from.email.trim().toLowerCase();
          const dark = darkMail;
          const showImgs = imgOk.has(m.uid);
          const remote = !!d?.html && hasRemoteContent(d.html);
          // Zitierten Verlauf standardmäßig abtrennen → je Karte nur der NEUE Text.
          const showQuote = quoteOk.has(m.uid);
          let bodyHtml = d?.html ?? "", bodyText = d?.text ?? "", hasQuote = false;
          if (isOpen && d) {
            if (d.html) { const r = trimQuotedHtml(d.html); hasQuote = r.trimmed; if (!showQuote && r.trimmed) bodyHtml = r.html; }
            else if (d.text) { const r = trimQuotedText(d.text); hasQuote = r.trimmed; if (!showQuote && r.trimmed) bodyText = r.text; }
          }
          // Aktions-Leiste (kompakt, nur Icons) — sitzt OBEN in der Absenderzeile.
          // stopPropagation am Container, damit ein Klick nicht die Karte zuklappt.
          const actionsBar = isOpen && d ? (
            <div className="thread-msg-actions" onClick={(e) => e.stopPropagation()}>
              <button className="ghost" onClick={() => onReply(d)} title={t("mail.reply")}>↩</button>
              <button className="ghost" onClick={() => onForward(d)} title={t("mail.forward")}>↪</button>
              {hasQuote && (
                <button className={`ghost ${showQuote ? "on" : ""}`} onClick={() => setQuoteOk((s) => { const n = new Set(s); if (n.has(m.uid)) n.delete(m.uid); else n.add(m.uid); return n; })}
                  title={showQuote ? t("mail.quoteHide") : t("mail.quoteShow")}>{showQuote ? "▴" : "···"}</button>
              )}
              {blockImages && !showImgs && remote && (
                <button className="ghost" onClick={() => setImgOk((s) => new Set(s).add(m.uid))} title={t("mail.showImages")}>🖼</button>
              )}
              {actions && (
                <span style={{ position: "relative" }}>
                  <button className={`ghost ${lblMenuKey === keyFor(m) ? "on" : ""}`} onClick={() => { setLblMenuKey((k) => k === keyFor(m) ? null : keyFor(m)); setMoreMenuKey(null); }} title={t("label.title")}>🏷</button>
                  {lblMenuKey === keyFor(m) && (
                    <>
                      <div className="menu-backdrop" onClick={() => setLblMenuKey(null)} />
                      <div className="read-menu label-menu">
                        {actions.labels.length === 0 && <div className="muted" style={{ fontSize: "0.8rem", padding: "0.2rem 0.4rem" }}>{t("label.none")}</div>}
                        {actions.labels.map((l) => {
                          const applied = (m.labels ?? []).includes(l.keyword);
                          return (
                            <div key={l.keyword} className="label-menu-row">
                              <button className="label-menu-toggle" onClick={() => actions.onLabel(m, l.keyword, !applied)}>
                                <span className="label-dot" style={{ background: l.color }} />
                                <span className="grow">{l.name}</span>
                                {applied && <span>✓</span>}
                              </button>
                            </div>
                          );
                        })}
                        <button className="link-btn" style={{ marginTop: "0.3rem" }} onClick={() => { setLblMenuKey(null); actions.onNewLabel(); }}>＋ {t("label.new")}</button>
                      </div>
                    </>
                  )}
                </span>
              )}
              {actions?.onSpam && <button className="ghost" onClick={() => actions.onSpam!(m)} title={t("mail.spam")}>🚫</button>}
              {actions && (
                <span style={{ position: "relative" }}>
                  <button className={`ghost ${moreMenuKey === keyFor(m) ? "on" : ""}`} onClick={() => { setMoreMenuKey((k) => k === keyFor(m) ? null : keyFor(m)); setLblMenuKey(null); }} title={t("mail.more")}>⋯</button>
                  {moreMenuKey === keyFor(m) && (
                    <>
                      <div className="menu-backdrop" onClick={() => setMoreMenuKey(null)} />
                      <div className="read-menu">
                        <button onClick={() => { setMoreMenuKey(null); actions.onAddContact(m); }}>👤 {t("mail.addContact")}</button>
                        <button onClick={() => { setMoreMenuKey(null); actions.onMarkUnread(m); }}>● {t("mail.markUnread")}</button>
                        <button className="read-menu-danger" onClick={() => { setMoreMenuKey(null); actions.onBlock(m); }}>🚫 {t("mail.blockSender")}</button>
                        {actions.folders.length > 1 && (
                          <label className="read-menu-move">
                            <span>📁 {t("mail.moveTo")}</span>
                            <select value="" onChange={(e) => { if (e.target.value) { actions.onMove(m, e.target.value); setMoreMenuKey(null); } }}>
                              <option value="">…</option>
                              {actions.folders.filter((f) => f !== (m.folder || folder)).map((f) => <option key={f} value={f}>{f}</option>)}
                            </select>
                          </label>
                        )}
                        <button onClick={() => { setMoreMenuKey(null); actions.onViewSource(m); }}>📄 {lang === "de" ? "Original anzeigen" : "View source"}</button>
                      </div>
                    </>
                  )}
                </span>
              )}
              <button className="ghost read-del" onClick={() => onDelete(m)} title={t("mail.delete")}>🗑</button>
            </div>
          ) : null;
          return (
            <div key={`${m.folder ?? ""}:${m.uid}:${m.message_id ?? ""}`} className={`thread-msg ${isOpen ? "open" : ""} ${m.seen ? "" : "unseen"}`}>
              <div className="thread-msg-head" role="button" tabIndex={0}
                onClick={() => toggle(m)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(m); } }}>
                <button className="thread-star" onClick={(e) => { e.stopPropagation(); onFlag(m); }} title={t("mail.flag")}>
                  {m.flagged ? "★" : "☆"}
                </button>
                <Avatar label={dispName} photo={avatarMap[from.email.trim().toLowerCase()]} />
                <div className="thread-msg-who">
                  <span className="thread-msg-name">{dispName}</span>
                  {!sameAddr && dispName !== from.email && <span className="thread-msg-addr">&lt;{from.email}&gt;</span>}
                  {!isOpen && m.snippet && <span className="thread-msg-snip">{m.snippet}</span>}
                </div>
                {m.has_attachments && <span className="thread-clip" title={t("mail.attachments")}>📎</span>}
                {actionsBar}
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
                      {bodyHtml ? (
                        <iframe title={`mail-${m.uid}`}
                          sandbox="allow-popups allow-popups-to-escape-sandbox allow-same-origin"
                          className="mail-body-frame thread-body-frame"
                          style={{ height: heights[m.uid] ? `${heights[m.uid]}px` : undefined }}
                          ref={(el) => { if (el) frameRefs.current.set(m.uid, el); else frameRefs.current.delete(m.uid); }}
                          onLoad={(e) => { const el = e.currentTarget; measure(m.uid, el); setTimeout(() => measure(m.uid, el), 180); }}
                          srcDoc={buildSrcDoc(bodyHtml, blockImages && !showImgs, dark)} />
                      ) : bodyText ? (
                        <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{bodyText}</div>
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
