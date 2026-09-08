import type { MsgHeader } from "./api";

/** UIDs sind nur innerhalb genau einer Konto-/Ordner-Generation eindeutig. */
export function messageKey(m: MsgHeader, accountId: number, folder: string): string {
  return JSON.stringify([m.account_id ?? accountId, m.folder || folder, m.uidvalidity ?? 0, m.uid]);
}

export function messageGeneration(m: Pick<MsgHeader, "uidvalidity">): string {
  return m.uidvalidity ? `&uidvalidity=${m.uidvalidity}` : "";
}
