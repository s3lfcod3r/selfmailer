// Schlanke i18n ohne Abhängigkeiten: Context + useT()-Hook + localStorage.
// Sprachen DE/EN. t(key, params) ersetzt {platzhalter} im String.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "de" | "en";

const LANG_KEY = "selfmailer.lang";

// Datums-Locale passend zur Sprache (für toLocaleString etc.).
export function dateLocale(lang: Lang): string {
  return lang === "de" ? "de-DE" : "en-US";
}

type Dict = Record<string, string>;

const de: Dict = {
  // Allgemein
  "common.loading": "Laden…",
  "common.add": "Hinzufügen",
  "common.delete": "Löschen",
  "common.remove": "Entfernen",
  "common.cancel": "Abbrechen",
  "common.label": "Bezeichnung",
  "common.email": "E-Mail",
  "common.password": "Passwort",
  "common.username": "Benutzername",
  "common.displayName": "Anzeigename",

  // Navigation / Shell
  "nav.mail": "Mail",
  "nav.calendar": "Kalender",
  "nav.contacts": "Kontakte",
  "nav.notes": "Notizen",
  "nav.sync": "Sync & Export",
  "nav.accounts": "Konten",
  "nav.admin": "Verwaltung",
  "shell.themeLight": "Helles Design",
  "shell.themeDark": "Dunkles Design",
  "shell.adminBadge": "Admin",
  "shell.logout": "Abmelden",
  "shell.langSwitch": "English",

  // Login
  "login.titleSetup": "Erstes Konto anlegen",
  "login.titleLogin": "Anmelden",
  "login.subSetup": "Lege den Admin-Zugang für SelfMailer an.",
  "login.subLogin": "Dein eigener Mail-Client.",
  "login.userLabel": "Benutzername / E-Mail",
  "login.adminToken": "Admin-Token (falls per Env gesetzt)",
  "login.submitSetup": "Konto anlegen",
  "login.submitLogin": "Anmelden",

  // Notes
  "notes.title": "Titel",
  "notes.bodyPlaceholder": "Notiz schreiben…",
  "notes.save": "Notiz speichern",
  "notes.empty": "Noch keine Notizen.",
  "notes.pin": "Anheften",

  // Accounts (Self-Service)
  "accounts.added": "Konto hinzugefügt.",
  "accounts.testing": "Teste…",
  "accounts.testOk": "OK – {n} Ordner gefunden.",
  "accounts.testErr": "Fehler: {error}",
  "accounts.new": "Neues Mailkonto",
  "accounts.emailAddress": "E-Mail-Adresse",
  "accounts.appPassword": "Passwort / App-Passwort",
  "accounts.imapHost": "IMAP-Host (z. B. imap.web.de)",
  "accounts.smtpHost": "SMTP-Host (z. B. smtp.web.de)",
  "accounts.save": "Konto speichern",
  "accounts.test": "Verbindung testen",
  "accounts.empty": "Noch kein Konto. Lege oben eines an.",

  // Compose
  "compose.needRecipient": "Mindestens einen Empfänger angeben.",
  "compose.new": "Neue Nachricht",
  "compose.to": "An (Komma-getrennt)",
  "compose.cc": "Cc",
  "compose.subject": "Betreff",
  "compose.body": "Nachricht…",
  "compose.sending": "Sende…",
  "compose.send": "Senden",
  "compose.attach": "Datei anhängen",
  "compose.tooLarge": "Anhänge zu groß (max. 20 MB gesamt).",
  "compose.replyIntro": "Am {date} schrieb {from}:",
  "compose.forwardHeader": "---------- Weitergeleitete Nachricht ----------",
  "compose.fwdFrom": "Von:",
  "compose.fwdDate": "Datum:",
  "compose.fwdSubject": "Betreff:",

  // Mail
  "mail.noAccount": "Noch kein Mailkonto verbunden. Lege unter „Konten“ eines an.",
  "mail.newMail": "✎ Neue Mail",
  "mail.inbox": "Posteingang",
  "mail.loadingMessages": "Lade Nachrichten…",
  "mail.back": "← Zurück",
  "mail.reply": "↩ Antworten",
  "mail.forward": "↪ Weiterleiten",
  "mail.noSubject": "(kein Betreff)",
  "mail.emptyBody": "(leer)",
  "mail.noMessages": "Keine Nachrichten.",
  "mail.folderLabel": "Ordner",
  "mail.delete": "Löschen",
  "mail.markRead": "Gelesen",
  "mail.markUnread": "Ungelesen",
  "mail.flag": "Markieren",
  "mail.confirmDelete": "Nachricht löschen bzw. in den Papierkorb verschieben?",
  "mail.attachments": "Anhänge",
  "mail.selectHint": "Nachricht auswählen",

  // Calendar
  "cal.needFields": "Titel, Beginn und Ende sind nötig.",
  "cal.new": "Neuer Termin",
  "cal.title": "Titel",
  "cal.location": "Ort",
  "cal.start": "Beginn",
  "cal.end": "Ende",
  "cal.empty": "Noch keine Termine.",

  // Contacts
  "contacts.search": "Suche (Name, E-Mail, Firma)…",
  "contacts.needNameOrEmail": "Name oder E-Mail angeben.",
  "contacts.new": "Neuer Kontakt",
  "contacts.firstName": "Vorname",
  "contacts.lastName": "Nachname",
  "contacts.phone": "Telefon",
  "contacts.org": "Firma / Organisation",
  "contacts.save": "Kontakt speichern",
  "contacts.empty": "Keine Kontakte gefunden.",
  "contacts.noName": "(ohne Namen)",

  // Sync & Export
  "sync.never": "noch nie",
  "sync.copied": "In die Zwischenablage kopiert.",
  "sync.rotateConfirm": "Neuen Token erzeugen? Bestehende Abos werden ungültig.",
  "sync.rotated": "Token rotiert.",
  "sync.needUrlPw": "URL und Passwort sind nötig.",
  "sync.result": "Sync „{label}“: {imported} neu, {updated} aktualisiert, {removed} entfernt.",
  "sync.failed": "Sync fehlgeschlagen: {error}",
  "sync.removeConfirm": "Konto „{label}“ und alle importierten Einträge löschen?",
  "sync.feedHeading": "Abonnier-Links (Handy-Kalender / Adressbuch)",
  "sync.feedHint":
    "Diese Links enthalten einen geheimen Token. Im Handy-Kalender als abonnierten Kalender bzw. im Adressbuch als CardDAV/Datei-Quelle eintragen.",
  "sync.feedCalendar": "Kalender (.ics)",
  "sync.feedContacts": "Kontakte (.vcf)",
  "sync.copy": "Kopieren",
  "sync.open": "Öffnen",
  "sync.regenToken": "Token neu erzeugen",
  "sync.externalHeading": "Externe CalDAV/CardDAV-Konten",
  "sync.caldavOption": "CalDAV (Kalender)",
  "sync.carddavOption": "CardDAV (Kontakte)",
  "sync.collectionUrl": "Collection-URL (z. B. https://nextcloud/remote.php/dav/calendars/user/personal/)",
  "sync.appToken": "Passwort / App-Token",
  "sync.externalEmpty": "Noch keine externen Konten verbunden.",
  "sync.kindCalendar": "Kalender",
  "sync.kindContacts": "Kontakte",
  "sync.lastSync": "Letzter Sync: {when}",
  "sync.syncing": "Synchronisiere…",
  "sync.syncNow": "Jetzt synchronisieren",

  // Admin
  "admin.created": "User „{name}“ angelegt.",
  "admin.resetPrompt": "Neues Passwort für {name} (min. 8 Zeichen):",
  "admin.pwSet": "Passwort gesetzt.",
  "admin.deleteConfirm": "User „{name}“ wirklich löschen?",
  "admin.newUser": "Neuen Benutzer anlegen",
  "admin.pwMin": "Passwort (min. 8)",
  "admin.roleUser": "Benutzer",
  "admin.roleAdmin": "Admin",
  "admin.create": "Anlegen",
  "admin.you": "Du",
  "admin.active": "aktiv",
  "admin.blocked": "gesperrt",
  "admin.accountsOpen": "Konten ▲",
  "admin.accountsClosed": "Konten…",
  "admin.pwButton": "Passwort…",
  "admin.block": "Sperren",
  "admin.unblock": "Entsperren",

  // UserAccounts (Admin-Unteransicht)
  "uacc.empty": "Noch kein Konto für diesen User.",
  "uacc.imapHost": "IMAP-Host",
  "uacc.smtpHost": "SMTP-Host",
  "uacc.createForUser": "Konto für User anlegen",
};

const en: Dict = {
  // Common
  "common.loading": "Loading…",
  "common.add": "Add",
  "common.delete": "Delete",
  "common.remove": "Remove",
  "common.cancel": "Cancel",
  "common.label": "Label",
  "common.email": "Email",
  "common.password": "Password",
  "common.username": "Username",
  "common.displayName": "Display name",

  // Navigation / Shell
  "nav.mail": "Mail",
  "nav.calendar": "Calendar",
  "nav.contacts": "Contacts",
  "nav.notes": "Notes",
  "nav.sync": "Sync & Export",
  "nav.accounts": "Accounts",
  "nav.admin": "Administration",
  "shell.themeLight": "Light theme",
  "shell.themeDark": "Dark theme",
  "shell.adminBadge": "Admin",
  "shell.logout": "Sign out",
  "shell.langSwitch": "Deutsch",

  // Login
  "login.titleSetup": "Create first account",
  "login.titleLogin": "Sign in",
  "login.subSetup": "Create the admin account for SelfMailer.",
  "login.subLogin": "Your own mail client.",
  "login.userLabel": "Username / Email",
  "login.adminToken": "Admin token (if set via env)",
  "login.submitSetup": "Create account",
  "login.submitLogin": "Sign in",

  // Notes
  "notes.title": "Title",
  "notes.bodyPlaceholder": "Write a note…",
  "notes.save": "Save note",
  "notes.empty": "No notes yet.",
  "notes.pin": "Pin",

  // Accounts (self-service)
  "accounts.added": "Account added.",
  "accounts.testing": "Testing…",
  "accounts.testOk": "OK – {n} folders found.",
  "accounts.testErr": "Error: {error}",
  "accounts.new": "New mail account",
  "accounts.emailAddress": "Email address",
  "accounts.appPassword": "Password / app password",
  "accounts.imapHost": "IMAP host (e.g. imap.web.de)",
  "accounts.smtpHost": "SMTP host (e.g. smtp.web.de)",
  "accounts.save": "Save account",
  "accounts.test": "Test connection",
  "accounts.empty": "No account yet. Create one above.",

  // Compose
  "compose.needRecipient": "Enter at least one recipient.",
  "compose.new": "New message",
  "compose.to": "To (comma-separated)",
  "compose.cc": "Cc",
  "compose.subject": "Subject",
  "compose.body": "Message…",
  "compose.sending": "Sending…",
  "compose.send": "Send",
  "compose.attach": "Attach file",
  "compose.tooLarge": "Attachments too large (max. 20 MB total).",
  "compose.replyIntro": "On {date} {from} wrote:",
  "compose.forwardHeader": "---------- Forwarded message ----------",
  "compose.fwdFrom": "From:",
  "compose.fwdDate": "Date:",
  "compose.fwdSubject": "Subject:",

  // Mail
  "mail.noAccount": "No mail account connected. Create one under “Accounts”.",
  "mail.newMail": "✎ New mail",
  "mail.inbox": "Inbox",
  "mail.loadingMessages": "Loading messages…",
  "mail.back": "← Back",
  "mail.reply": "↩ Reply",
  "mail.forward": "↪ Forward",
  "mail.noSubject": "(no subject)",
  "mail.emptyBody": "(empty)",
  "mail.noMessages": "No messages.",
  "mail.folderLabel": "Folder",
  "mail.delete": "Delete",
  "mail.markRead": "Mark read",
  "mail.markUnread": "Unread",
  "mail.flag": "Star",
  "mail.confirmDelete": "Delete message / move to trash?",
  "mail.attachments": "Attachments",
  "mail.selectHint": "Select a message",

  // Calendar
  "cal.needFields": "Title, start and end are required.",
  "cal.new": "New event",
  "cal.title": "Title",
  "cal.location": "Location",
  "cal.start": "Start",
  "cal.end": "End",
  "cal.empty": "No events yet.",

  // Contacts
  "contacts.search": "Search (name, email, company)…",
  "contacts.needNameOrEmail": "Enter a name or email.",
  "contacts.new": "New contact",
  "contacts.firstName": "First name",
  "contacts.lastName": "Last name",
  "contacts.phone": "Phone",
  "contacts.org": "Company / organization",
  "contacts.save": "Save contact",
  "contacts.empty": "No contacts found.",
  "contacts.noName": "(no name)",

  // Sync & Export
  "sync.never": "never",
  "sync.copied": "Copied to clipboard.",
  "sync.rotateConfirm": "Generate a new token? Existing subscriptions become invalid.",
  "sync.rotated": "Token rotated.",
  "sync.needUrlPw": "URL and password are required.",
  "sync.result": "Sync “{label}”: {imported} new, {updated} updated, {removed} removed.",
  "sync.failed": "Sync failed: {error}",
  "sync.removeConfirm": "Delete account “{label}” and all imported entries?",
  "sync.feedHeading": "Subscription links (phone calendar / address book)",
  "sync.feedHint":
    "These links contain a secret token. Add them to your phone calendar as a subscribed calendar, or to your address book as a CardDAV/file source.",
  "sync.feedCalendar": "Calendar (.ics)",
  "sync.feedContacts": "Contacts (.vcf)",
  "sync.copy": "Copy",
  "sync.open": "Open",
  "sync.regenToken": "Regenerate token",
  "sync.externalHeading": "External CalDAV/CardDAV accounts",
  "sync.caldavOption": "CalDAV (calendar)",
  "sync.carddavOption": "CardDAV (contacts)",
  "sync.collectionUrl": "Collection URL (e.g. https://nextcloud/remote.php/dav/calendars/user/personal/)",
  "sync.appToken": "Password / app token",
  "sync.externalEmpty": "No external accounts connected yet.",
  "sync.kindCalendar": "Calendar",
  "sync.kindContacts": "Contacts",
  "sync.lastSync": "Last sync: {when}",
  "sync.syncing": "Syncing…",
  "sync.syncNow": "Sync now",

  // Admin
  "admin.created": "User “{name}” created.",
  "admin.resetPrompt": "New password for {name} (min. 8 chars):",
  "admin.pwSet": "Password set.",
  "admin.deleteConfirm": "Really delete user “{name}”?",
  "admin.newUser": "Create new user",
  "admin.pwMin": "Password (min. 8)",
  "admin.roleUser": "User",
  "admin.roleAdmin": "Admin",
  "admin.create": "Create",
  "admin.you": "You",
  "admin.active": "active",
  "admin.blocked": "blocked",
  "admin.accountsOpen": "Accounts ▲",
  "admin.accountsClosed": "Accounts…",
  "admin.pwButton": "Password…",
  "admin.block": "Block",
  "admin.unblock": "Unblock",

  // UserAccounts (admin sub-view)
  "uacc.empty": "No account for this user yet.",
  "uacc.imapHost": "IMAP host",
  "uacc.smtpHost": "SMTP host",
  "uacc.createForUser": "Create account for user",
};

const DICTS: Record<Lang, Dict> = { de, en };

export type TFunc = (key: string, params?: Record<string, string | number>) => string;

function translate(lang: Lang, key: string, params?: Record<string, string | number>): string {
  const raw = DICTS[lang][key] ?? DICTS.de[key] ?? key;
  if (!params) return raw;
  return raw.replace(/\{(\w+)\}/g, (_, name) =>
    name in params ? String(params[name]) : `{${name}}`,
  );
}

type LangContextValue = { lang: Lang; setLang: (l: Lang) => void; t: TFunc };

const LangContext = createContext<LangContextValue | null>(null);

function initialLang(): Lang {
  const stored = localStorage.getItem(LANG_KEY);
  if (stored === "de" || stored === "en") return stored;
  return navigator.language?.toLowerCase().startsWith("en") ? "en" : "de";
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(initialLang);

  useEffect(() => {
    localStorage.setItem(LANG_KEY, lang);
    document.documentElement.setAttribute("lang", lang);
  }, [lang]);

  const value: LangContextValue = {
    lang,
    setLang: setLangState,
    t: (key, params) => translate(lang, key, params),
  };
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangContextValue {
  const ctx = useContext(LangContext);
  if (!ctx) throw new Error("useLang must be used within LangProvider");
  return ctx;
}
