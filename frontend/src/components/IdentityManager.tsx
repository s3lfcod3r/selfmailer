import { useEffect, useState } from "react";
import { api, type Identity } from "../lib/api";
import { useLang } from "../lib/i18n";
import { confirmDialog } from "../lib/dialog";
import { RichEditor } from "./RichEditor";

type Draft = { name: string; email: string; signature: string; is_default: boolean };
const EMPTY: Draft = { name: "", email: "", signature: "", is_default: false };

/**
 * Verwaltet die Absender-Identitäten/Aliase eines Kontos (Anlegen/Bearbeiten/Löschen).
 * Eine Identität setzt beim Schreiben From-Adresse, Anzeigename und eine eigene Signatur.
 * Wird im Bearbeiten-Bereich jeder Konto-Karte eingebettet.
 */
export function IdentityManager({ accountId }: { accountId: number }) {
  const { t } = useLang();
  const [items, setItems] = useState<Identity[]>([]);
  const [editId, setEditId] = useState<number | null>(null); // null=kein Edit, 0=neu
  const [draft, setDraft] = useState<Draft>({ ...EMPTY });
  const [err, setErr] = useState("");

  async function load() {
    try {
      const all = await api.get<Identity[]>("/identities");
      setItems(all.filter((i) => i.account_id === accountId));
    } catch (e) { setErr((e as Error).message); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [accountId]);

  function startNew() { setEditId(0); setDraft({ ...EMPTY }); setErr(""); }
  function startEdit(i: Identity) {
    setEditId(i.id);
    setDraft({ name: i.name, email: i.email, signature: i.signature, is_default: i.is_default });
    setErr("");
  }
  async function save() {
    setErr("");
    if (!draft.email.trim()) { setErr(t("identities.needEmail")); return; }
    try {
      if (editId && editId > 0) {
        await api.patch<Identity>(`/identities/${editId}`, draft);
      } else {
        await api.post<Identity>("/identities", { account_id: accountId, ...draft });
      }
      setEditId(null); await load();
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(i: Identity) {
    if (!(await confirmDialog(t("identities.confirmDelete", { name: i.name || i.email })))) return;
    try { await api.del(`/identities/${i.id}`); await load(); }
    catch (e) { setErr((e as Error).message); }
  }

  return (
    <fieldset className="acc-fieldset">
      <legend>👤 {t("identities.section")}</legend>
      <p className="mail-from" style={{ margin: "0 0 0.6rem" }}>{t("identities.hint")}</p>
      {err && <div className="err" style={{ marginBottom: "0.5rem" }}>{err}</div>}

      <div className="stack" style={{ gap: "0.4rem" }}>
        {items.map((i) => (
          <div className="row" key={i.id} style={{ alignItems: "center", gap: "0.5rem" }}>
            <span className="grow" style={{ minWidth: 0 }}>
              {i.name ? `${i.name} — ${i.email}` : i.email}
              {i.is_default && <span className="badge" style={{ marginLeft: "0.4rem" }}>{t("identities.default")}</span>}
            </span>
            <button type="button" className="ghost" onClick={() => startEdit(i)}>✎</button>
            <button type="button" className="ghost" onClick={() => remove(i)} title={t("common.remove")}>🗑</button>
          </div>
        ))}
        {items.length === 0 && <p className="muted" style={{ margin: 0 }}>{t("identities.empty")}</p>}
      </div>

      {editId !== null ? (
        <div className="stack" style={{ gap: "0.5rem", marginTop: "0.7rem" }}>
          <div className="acc-grid">
            <label className="stack"><span className="label">{t("identities.name")}</span>
              <input value={draft.name} onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))} /></label>
            <label className="stack"><span className="label">{t("identities.email")}</span>
              <input type="email" value={draft.email}
                onChange={(e) => setDraft((d) => ({ ...d, email: e.target.value }))} required /></label>
          </div>
          <div className="stack" style={{ gap: "0.35rem" }}>
            <span className="label">✍ {t("accounts.signature")}</span>
            <RichEditor value={draft.signature}
              onChange={(html) => setDraft((d) => ({ ...d, signature: html }))}
              placeholder={t("accounts.signaturePlaceholder")} />
          </div>
          <label className="acc-check">
            <input type="checkbox" checked={draft.is_default}
              onChange={(e) => setDraft((d) => ({ ...d, is_default: e.target.checked }))} />
            {t("identities.setDefault")}
          </label>
          <div className="row">
            <span className="grow" />
            <button type="button" className="ghost" onClick={() => setEditId(null)}>{t("common.cancel")}</button>
            <button type="button" className="primary" onClick={save}>{t("accounts.save")}</button>
          </div>
        </div>
      ) : (
        <button type="button" className="ghost" style={{ marginTop: "0.6rem" }} onClick={startNew}>
          ＋ {t("identities.add")}
        </button>
      )}
    </fieldset>
  );
}
