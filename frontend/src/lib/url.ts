// Sichere Aufbereitung von Nutzer-eingegebenen Link-URLs fuer den Editor.
// Verhindert, dass `javascript:`, `data:` oder `vbscript:` als href landen
// (XSS ueber den "Link einfuegen"-Dialog).

const _SAFE_SCHEME = /^(https?:|mailto:|tel:)/i;
const _ANY_SCHEME = /^[a-z][a-z0-9+.-]*:/i;

/**
 * Gibt eine sichere URL zurueck oder null, wenn die Eingabe leer oder das
 * Schema nicht erlaubt ist. Schemalose Eingaben ("example.com") werden mit
 * https:// versehen.
 */
export function safeLinkUrl(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const url = raw.trim();
  if (!url) return null;
  if (_SAFE_SCHEME.test(url)) return url;
  // Hat ein (anderes) Schema → ablehnen (javascript:, data:, vbscript:, file: …).
  if (_ANY_SCHEME.test(url)) return null;
  // Schemalos → als https-Webadresse interpretieren.
  return `https://${url}`;
}
