# LAN HTTPS mode (headless appliance)

PLAN.md sections 10.1 and 14. By default the appliance serves the UI only at `http://localhost:8080` on the device itself. **LAN HTTPS mode** is opt-in. It lets you run the setup wizard and use the UI from another computer or a phone on the same network. In this mode:

- The proxy listens on **443** with a certificate from a **device-local certificate authority** (CA).
- HTTP (port 80 by default) redirects to HTTPS.
- Session cookies are always `Secure`.

## Turn it on

```bash
sudo crewquarters lan-https enable                       # name: this host's name
sudo crewquarters lan-https enable --hostname spark      # or choose the name
```

Options:

- `--ip ADDR` (repeatable): the LAN addresses the certificate covers. Default: every private IPv4 address of the host, excluding container bridges.
- `--bind ADDR`: the listening address. Default: `0.0.0.0`.
- `--http-port PORT`: the port for the HTTP-to-HTTPS redirect. Default: 80.
- `--no-restart`: write the configuration only, and don't apply it to a running stack.

On a headless install you can enable it at install time:

```bash
sudo CREWQUARTERS_HEADLESS=yes CREWQUARTERS_LAN_HOSTNAME=spark apt install ./crewquarters_<version>_arm64.deb
```

Without `CREWQUARTERS_HEADLESS=yes`, the installer never exposes the device on the LAN. It only prints a hint when no desktop is detected.

`enable` prints:

- the address, for example `https://spark.local/setup` and `https://192.168.1.20/setup`;
- the **CA fingerprint** (SHA-256);
- where to download the CA;
- how to get the one-time owner code (`sudo crewquarters bootstrap-token`).

### What `enable` does

| Step | Detail |
| --- | --- |
| Device CA | Created once in `/etc/crewquarters/tls/`: `ca.key` (`root:root 0600`, never mounted into any container) and `ca.crt`. EC P-256, valid 10 years. **Name-constrained** to `NAME`, `NAME.local`, `localhost` and private IPv4 ranges, so even a leaked CA key cannot impersonate any other site to a device that trusts it. It is reused on every re-run with the same name, so devices keep trusting it. |
| Server certificate | `server.crt` and `server.key` (`root:crewquarters 0640`; the proxy reads it through the group). SANs: `NAME`, `NAME.local` (for a single-label name), `localhost`, `127.0.0.1` and the LAN addresses. Valid 825 days, the maximum Apple platforms accept. Re-run `enable` to renew it or to add a new address. |
| Settings | Writes `/etc/crewquarters/lan-https.env`. `crewquarters` passes it after `crewquarters.env`, which is left untouched: `CQ_BIND_ADDRESS=0.0.0.0`, `CQ_HTTP_PORT=80`, `CQ_PUBLIC_BASE_URL=https://NAME.local`, `CQ_PUBLIC_ORIGINS=[every https:// name and address]`, `CQ_COOKIE_SECURE=true`. |
| Proxy | Adds `compose.lan-https.yaml`. nginx runs `nginx-lan-https.conf`. |
| Launcher | Writes `/var/lib/crewquarters/public/ui-url` and a public copy of `ca.crt` for the desktop launcher. |
| Apply | Recreates the changed containers if the stack is running. |

### What the proxy serves in this mode

| Listener | Behavior |
| --- | --- |
| `:443` (container 8443), TLS | The same routes as the default mode ([proxy.md](proxy.md)). TLS 1.2 and 1.3 only, with ECDHE AEAD ciphers only (Mozilla "intermediate"), HTTP/2, and no session tickets. `Strict-Transport-Security: max-age=31536000`, sent only over HTTPS to a host name, never to `localhost` or an IP address. The CA is also at `/crewquarters-ca.crt`. |
| `CQ_HTTP_PORT` (80) | `301` to `https://<same host><same path>`, except `/crewquarters-ca.crt`, so a phone can fetch the CA before it trusts it. |
| `:8081` callbacks | Unchanged. The tunnel terminates TLS itself. |

## Trust the device CA

Until a device trusts the CA, its browser shows a certificate warning. **Always compare the fingerprint** of the file you downloaded with the one `enable` printed (or `sudo crewquarters lan-https status`). The download travels over plain HTTP, so the fingerprint is what proves it came from your device.

Get the file:

- From any device, open `http://NAME.local/crewquarters-ca.crt`, or `http://<LAN address>/crewquarters-ca.crt`.
- On the device itself, copy `/var/lib/crewquarters/public/ca.crt`.

To check a fingerprint on a computer:

```bash
openssl x509 -in crewquarters-ca.crt -noout -fingerprint -sha256
```

| Platform | Steps |
| --- | --- |
| **macOS** | Double-click the file. Keychain Access adds it to the *login* keychain. Open it, expand **Trust**, and set **When using this certificate** to **Always Trust**. Or run `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain crewquarters-ca.crt`. The fingerprint is shown as SHA-256 in the certificate details. Safari and Chrome use the keychain. Firefox needs `security.enterprise_roots.enabled` set to `true` (the default on recent versions) or an import in its own settings. |
| **Windows** | Double-click the file, then **Install Certificate**, **Local Machine**, and **Place all certificates in the following store**: *Trusted Root Certification Authorities*. Or, as administrator, run `certutil -addstore -f ROOT crewquarters-ca.crt`. Check the fingerprint with `certutil -hashfile crewquarters-ca.crt SHA256`; the hash covers the PEM file, so compare the certificate's *Thumbprint* instead, or use `openssl` as above. Edge and Chrome use this store. |
| **iOS / iPadOS** | Open `http://NAME.local/crewquarters-ca.crt` in **Safari** and allow the profile download. Then go to **Settings**, **General**, **VPN & Device Management**, select the profile, and tap **Install**. Then enable full trust: **Settings**, **General**, **About**, **Certificate Trust Settings**, and turn on the Crewquarters CA. The profile view shows the SHA-256 fingerprint. |
| **Android** | Download the file, then go to **Settings**, **Security** (or **Security & privacy**), **More security settings** or **Encryption & credentials**, **Install a certificate**, **CA certificate**, and pick the file. Menu names vary by vendor. Chrome trusts user-installed CAs. Many apps do not, which is fine: only the browser needs it. Android browsers may not resolve `.local` names, so use `https://<LAN address>`. |
| **Linux** | System store (curl, most tools, and Chrome on some distributions): `sudo cp crewquarters-ca.crt /usr/local/share/ca-certificates/crewquarters-NAME.crt && sudo update-ca-certificates`. Chrome and Chromium use NSS: `certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "Crewquarters NAME" -i crewquarters-ca.crt` (package `libnss3-tools`). Firefox: **Settings**, **Privacy & Security**, **Certificates**, **View Certificates**, **Authorities**, **Import**, then tick "Trust this CA to identify websites". |

To stop trusting it, remove it from the same place. Name constraints limit the CA to this device's names and to private addresses.

## Names and addresses

- `NAME.local` resolves on the LAN through mDNS when `NAME` is the device's host name. DGX OS and Ubuntu desktop run Avahi. For a different name, add a DNS entry or a hosts-file line on the clients.
- If an address changes (DHCP), re-run `enable`. It adds the address to the certificate and to `CQ_PUBLIC_ORIGINS`. Otherwise the control API refuses sign-in from the new `https://<address>` origin. A DHCP reservation avoids this.
- A dotted name (`--hostname spark.example.com`) is used as is, with no `.local` twin, and becomes `CQ_PUBLIC_BASE_URL`.

## Google sign-in and Twilio in this mode

- **Google** redirects the browser to `CQ_PUBLIC_BASE_URL` + `/api/v1/connections/google/callback`, which the Connections page shows. Google Cloud accepts only `localhost` or a **public top-level domain** for web redirect URIs, so it rejects `https://spark.local/...`. Choose one:
  - Use `--hostname` with a real DNS name that you control, pointing at the device's LAN address (for example `spark.example.com`). Register that URI and open the UI through that name.
  - Connect Google once from a browser on the device with LAN HTTPS off (`http://localhost:8080`), then turn LAN HTTPS on. The refresh token stays valid. Test-mode tokens expire after 7 days; reconnect the same way.
- **Twilio** needs a public HTTPS URL, which the LAN name is not. Use the callback tunnel and set `CQ_TWILIO_CALLBACK_BASE_URL` to the tunnel's origin ([proxy.md](proxy.md#callback-exposure-profile-callbacks)). That setting is independent of LAN HTTPS mode.

## Desktop launcher

The **Crewquarters** desktop entry (`/usr/share/crewquarters/launch.sh`, unprivileged) works the same way in both modes:

- It waits for `GET /api/v1/health/ready` and shows a "starting" notification while it waits.
- It opens `/setup` the first time and `/` afterwards. The UI itself resumes an unfinished setup.
- In LAN HTTPS mode it checks readiness over HTTPS against the device CA.
- If the device cannot resolve its own `NAME.local`, it opens `https://localhost`, which the certificate also covers.

## Turn it off

```bash
sudo crewquarters lan-https disable              # back to http://localhost:8080; CA kept
sudo crewquarters lan-https disable --forget-ca  # also delete the CA and certificate
sudo crewquarters lan-https status
```

`disable` removes `lan-https.env` and re-applies the stack, so port 443 and the LAN port close. Keeping the CA means that if you enable the mode again, devices that already trust it still do. `apt purge` deletes the CA. After `--forget-ca` or a purge, remove the old CA from every device that trusted it.

## Verify

```bash
sudo crewquarters lan-https status
curl --cacert /var/lib/crewquarters/public/ca.crt https://spark.local/api/v1/health/ready
curl -sI http://spark.local/setup           # 301 Location: https://spark.local/setup
```

Tests:

- `infra/proxy/test-proxy.sh <proxy image>` checks TLS, HSTS, the redirect, routing and the CA download against stub backends.
- `infra/debian/test-install.sh` checks enable, disable, file permissions, the launcher and headless installs inside `ubuntu:22.04`.
