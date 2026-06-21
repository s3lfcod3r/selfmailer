# HTTPS for SelfMailer (Reverse Proxy)

SelfMailer serves plain **HTTP** on port `8090` — fine inside a trusted LAN. To reach it
securely from outside (or to get a green padlock and enable browser features that require a
secure context), put a **reverse proxy** with a **Let's Encrypt** certificate in front.

[English](#english) · [Deutsch](#deutsch)

---

<a id="english"></a>

## 🇬🇧 English

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

### After enabling HTTPS

- Change the server URL **in the web bookmark and in the app** (first-run / *Settings → change server*) to `https://mail.example.com`.
- You can now safely expose it (ideally still behind **WireGuard/Tailscale** for a private setup).
- HSTS and other strict headers become meaningful once TLS is enforced at the proxy.

---

<a id="deutsch"></a>

## 🇩🇪 Deutsch

SelfMailer liefert auf Port `8090` nur **HTTP** aus — im vertrauenswürdigen LAN okay. Für
**sicheren Zugriff von außen** (oder ein „grünes Schloss") setzt du einen **Reverse-Proxy**
mit **Let's-Encrypt**-Zertifikat davor.

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

### Nach dem Umstellen auf HTTPS

- Server-URL **im Browser-Lesezeichen und in der App** (Erststart / *Einstellungen → Server ändern*) auf `https://mail.example.com` ändern.
- Jetzt kannst du es gefahrlos erreichbar machen (für ein privates Setup am besten weiter **hinter WireGuard/Tailscale**).
- HSTS und strikte Header ergeben erst mit TLS am Proxy Sinn.
