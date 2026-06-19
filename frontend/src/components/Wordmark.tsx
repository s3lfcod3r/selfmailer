// SelfMailer-Wortmarke im Brand-Look: "Self" eis-blau + "Mailer" teal.
export function Wordmark({ size = 1.4 }: { size?: number }) {
  return (
    <div
      style={{
        fontFamily: "var(--self-font-wordmark)",
        fontWeight: 800,
        fontSize: `${size}rem`,
        letterSpacing: "0.02em",
        lineHeight: 1,
      }}
    >
      <span style={{ color: "var(--self-ice-bright)" }}>Self</span>
      <span style={{ color: "var(--self-teal)" }}>Mailer</span>
    </div>
  );
}
