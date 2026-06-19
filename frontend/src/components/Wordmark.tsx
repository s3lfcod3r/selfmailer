// SelfMailer-Wortmarke im Brand-Look (WIDE-Variante): Schild-Emblem + Schriftzug
// "Self" eis-blau + "Mailer" teal in Rubik 800.
export function Wordmark({ size = 1.4 }: { size?: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: `${size * 0.45}rem` }}>
      <img
        src="/shield.png"
        alt=""
        aria-hidden
        style={{ height: `${size * 1.5}rem`, width: "auto", display: "block" }}
      />
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
    </div>
  );
}
