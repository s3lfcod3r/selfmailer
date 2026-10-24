# HTTP or HTTPS for SelfMailer — your choice

SelfMailer always serves plain **HTTP** on port `8090` — fine inside a trusted LAN or behind a
VPN. **HTTPS is optional** and can run *alongside* HTTP (since 1.97.0): use a reverse proxy
(options A–C) or let the container serve HTTPS itself (option D).

[English](#english) · [Deutsch](#deutsch)

---

<a id="english"></a>

## 🇬🇧 English

### Which way fits?

| Setup | What to configure |
|---|---|
| **HTTP only** (LAN / VPN) | Nothing. Defaults work. |
| **Reverse proxy** with HTTPS (A–C) | `SELFMAILER_TRUSTED_PROXIES=<proxy IP>` (real client IP for the login rate limit, HTTPS detection). Optional `SELFMAILER_HSTS_MAX_AGE`. |
| **HTTPS in the container** (D) | `SELFMAILER_TLS_CERT` + `SELFMAILER_TLS_KEY`, map port `8443`. |

`SELFMAILER_COOKIE_SECURE=auto` (default) marks the login cookie *Secure* only on HTTPS
requests — so `http://192.168.x.y:8090` in the LAN and `https://mail.example.com` from outside
work **at the same time**, without login loops. Old values `true`/`false` keep working.

### Option A — Nginx Proxy Manager (recommended on Unraid)

1. Install **Nginx Proxy Manager** (NPM) from Community Apps. Open its admin UI.
2. Point a (sub)domain at your server, e.g. `mail.example.com` → your public IP (or use a
   DNS provider supported by NPM for DNS-01).
3. **Hosts → Proxy Hosts → Add Proxy Host**
   - **Domain Names:** `mail.example.com`
   - **Scheme:** `http`
   - **Forward Hostname / IP:** the SelfMailer container IP (or the Unraid host IP)
   - **Forward Port:** `8090`
   - **Block Common Exploits:** on · **Websockets Support:** on
4. **SSL tab → Request a new SSL certificate** (Let's Encrypt) → Force SSL · HTTP/2 · HSTS.
5. Save. SelfMailer is now at `https://mail.example.com`.

> ⚠️ **Live sync (SSE) needs un-buffered streaming.** SelfMailer's `/api/v1/events/stream`
> is a long-lived Server-Sent-Events connection. By default nginx buffers responses and
> times out idle ones, which **breaks live sync**. In the proxy host's **Advanced** tab add:
>
> ```nginx
> location /api/v1/events/stream {
>     proxy_pass http://<FORWARD_IP>:8090;
>     proxy_http_version 1.1;
>     proxy_set_header Connection "";
>     proxy_buffering off;
>     proxy_cache off;
>     proxy_read_timeout 1h;
>     chunked_transfer_encoding off;
> }
> ```
> Replace `<FORWARD_IP>` with the same forward host you used above. (SelfMailer already sends
> `X-Accel-Buffering: no`, but the explicit location is the reliable fix.)

### Option B — Traefik

Add labels to the SelfMailer container:

```yaml
traefik.enable=true
traefik.http.routers.selfmailer.rule=Host(`mail.example.com`)
traefik.http.routers.selfmailer.entrypoints=websecure
traefik.http.routers.selfmailer.tls.certresolver=le
traefik.http.services.selfmailer.loadbalancer.server.port=8090
```

Traefik streams SSE fine by default (no response buffering). Nothing extra needed.

### Option C — Caddy

```caddyfile
mail.example.com {
    reverse_proxy <FORWARD_IP>:8090
}
```

Caddy handles HTTPS and SSE automatically — the simplest option if you don't already run NPM/Traefik.

> **For A–C:** set `SELFMAILER_TRUSTED_PROXIES` to the proxy's IP as seen by the container
> (e.g. the NPM container IP or the Docker bridge network `172.17.0.0/16`). Otherwise every
> user behind the proxy looks like one IP and they share one login rate limit, and SelfMailer
> can't tell that the request was HTTPS.

### Option D — HTTPS directly in the container (no proxy)

1. Put certificate and key (PEM) into the data folder, e.g. `data/certs/fullchain.pem` and
   `data/certs/privkey.pem` (any CA; for a LAN a self-signed or internal-CA cert works).
2. Set `SELFMAILER_TLS_CERT=/data/certs/fullchain.pem` and
   `SELFMAILER_TLS_KEY=/data/certs/privkey.pem`, map port `8443` (Unraid: *HTTPS Port*).
3. Restart. HTTPS now listens on `8443` **in addition to** HTTP on `8090`.
   Want HTTPS only? Simply don't map port `8090`.

The files must be readable by the container user (uid 99). SelfMailer does not renew
certificates — replace the files and restart the container.

### After enabling HTTPS

- Change the server URL **in the web bookmark and in the app** (first-run / *Settings → change server*) to the `https://` address.
- Optional: `SELFMAILER_HSTS_MAX_AGE=31536000` — sent **only** on HTTPS responses, so plain
  HTTP in the LAN keeps working (as long as you use a different host name, e.g. the IP).
- You can now expose it more safely (ideally still behind **WireGuard/Tailscale** for a private setup).

---

<a id="deutsch"></a>

## 🇩🇪 Deutsch

SelfMailer liefert auf Port `8090` immer **HTTP** aus — im vertrauenswürdigen LAN oder hinter
einem VPN okay. **HTTPS ist optional** und läuft seit 1.97.0 *zusätzlich* zu HTTP: per
Reverse-Proxy (Varianten A–C) oder direkt im Container (Variante D).

### Welcher Weg passt?

| Aufbau | Was einstellen |
|---|---|
| **Nur HTTP** (LAN / VPN) | Nichts. Die Standardwerte passen. |
| **Reverse-Proxy** mit HTTPS (A–C) | `SELFMAILER_TRUSTED_PROXIES=<Proxy-IP>` (echte Client-IP für die Login-Bremse, HTTPS-Erkennung). Optional `SELFMAILER_HSTS_MAX_AGE`. |
| **HTTPS im Container** (D) | `SELFMAILER_TLS_CERT` + `SELFMAILER_TLS_KEY`, Port `8443` freigeben. |

`SELFMAILER_COOKIE_SECURE=auto` (Standard) setzt das *Secure*-Flag des Login-Cookies nur bei
HTTPS-Anfragen — `http://192.168.x.y:8090` im LAN und `https://mail.example.com` von außen
funktionieren also **gleichzeitig**, ohne Login-Schleife. Die alten Werte `true`/`false` gelten weiter.

### Variante A — Nginx Proxy Manager (auf Unraid empfohlen)

1. **Nginx Proxy Manager** (NPM) aus Community Apps installieren, Admin-UI öffnen.
2. Eine (Sub-)Domain auf deinen Server zeigen lassen, z. B. `mail.example.com`.
3. **Hosts → Proxy Hosts → Add Proxy Host**
   - **Domain Names:** `mail.example.com`
   - **Scheme:** `http`
   - **Forward Hostname / IP:** Container- bzw. Unraid-Host-IP
   - **Forward Port:** `8090`
   - **Block Common Exploits:** an · **Websockets Support:** an
4. **SSL-Tab → Request a new SSL certificate** (Let's Encrypt) → Force SSL · HTTP/2 · HSTS.
5. Speichern → SelfMailer ist unter `https://mail.example.com` erreichbar.

> ⚠️ **Der Live-Sync (SSE) darf nicht gepuffert werden.** `/api/v1/events/stream` ist eine
> Dauerverbindung (Server-Sent Events). nginx puffert standardmäßig und kappt Leerlauf —
> das **bricht den Live-Sync**. Im **Advanced**-Tab des Proxy-Hosts ergänzen:
>
> ```nginx
> location /api/v1/events/stream {
>     proxy_pass http://<FORWARD_IP>:8090;
>     proxy_http_version 1.1;
>     proxy_set_header Connection "";
>     proxy_buffering off;
>     proxy_cache off;
>     proxy_read_timeout 1h;
>     chunked_transfer_encoding off;
> }
> ```
> `<FORWARD_IP>` durch dieselbe Forward-Adresse wie oben ersetzen. (SelfMailer sendet bereits
> `X-Accel-Buffering: no`, aber die explizite Location ist der sichere Weg.)

### Variante B — Traefik

Labels am SelfMailer-Container (siehe oben). Traefik streamt SSE standardmäßig ohne Puffern —
kein Zusatz nötig.

### Variante C — Caddy

```caddyfile
mail.example.com {
    reverse_proxy <FORWARD_IP>:8090
}
```

Caddy macht HTTPS **und** SSE automatisch — die einfachste Variante, wenn du nicht eh NPM/Traefik hast.

> **Für A–C:** `SELFMAILER_TRUSTED_PROXIES` auf die IP des Proxys setzen, so wie der Container
> sie sieht (z. B. die IP des NPM-Containers oder das Docker-Netz `172.17.0.0/16`). Sonst sehen
> alle Nutzer hinter dem Proxy wie EINE IP aus und teilen sich eine Login-Bremse, und
> SelfMailer erkennt nicht, dass die Anfrage per HTTPS kam.

### Variante D — HTTPS direkt im Container (ohne Proxy)

1. Zertifikat und Schlüssel (PEM) in den Datenordner legen, z. B. `data/certs/fullchain.pem`
   und `data/certs/privkey.pem` (beliebige CA; im LAN geht auch ein selbstsigniertes oder ein
   Zertifikat einer eigenen CA).
2. `SELFMAILER_TLS_CERT=/data/certs/fullchain.pem` und
   `SELFMAILER_TLS_KEY=/data/certs/privkey.pem` setzen, Port `8443` freigeben
   (Unraid: *HTTPS Port*).
3. Neu starten. HTTPS lauscht jetzt auf `8443` **zusätzlich** zu HTTP auf `8090`.
   Nur HTTPS gewünscht? Port `8090` einfach nicht freigeben.

Die Dateien müssen für den Container-Nutzer (uid 99) lesbar sein. SelfMailer erneuert
Zertifikate nicht selbst — Dateien austauschen und den Container neu starten.

### Nach dem Umstellen auf HTTPS

- Server-URL **im Browser-Lesezeichen und in der App** (Erststart / *Einstellungen → Server ändern*) auf die `https://`-Adresse ändern.
- Optional: `SELFMAILER_HSTS_MAX_AGE=31536000` — wird **nur** auf HTTPS-Antworten gesendet,
  HTTP im LAN bleibt also möglich (solange du dort einen anderen Hostnamen, z. B. die IP, nutzt).
- Jetzt kannst du es sicherer erreichbar machen (für ein privates Setup am besten weiter **hinter WireGuard/Tailscale**).
