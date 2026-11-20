# Railway → Tailscale → Sunnyslope Pi (read-only connectivity phase)

The Raspberry Pi is **already a member of the owner's Tailscale tailnet**. It does not need Tailscale installation again.

## Deployment created

- Railway project: `ram-drive-log-bridge`, production.
- Separate service: **`ram-pi-tailnet-probe`** (service ID `8b4679cd-33ca-4363-b96c-a2dcd95eaacf`).
- Source branch `ram1500-tailnet-railway-probe-20261119` pinned to commit `213b5c7703714ecee5502ca38a8d2cb0178c058d`.
- Build: `ram1500-development/tailnet_bridge/Dockerfile` (Alpine, Python, Tailscale; userspace SOCKS5, **no TUN privileges**).
- Private Railway variable `TAILNET_PI_IP` is configured to the existing Pi's tailnet address. Do not publish this network locator or other private device identifiers in GitHub or runtime logs.
- **`TS_AUTHKEY` is not configured yet.** Current service logs deliberately say `awaiting_railway_tailscale_auth`; this is not a successful network connection.

## The only user action to enroll Railway

1. In the owner's Tailscale admin console, open **Settings → Keys → Generate auth key**. Choose a short-lived, appropriately scoped **reusable** key for a cloud workload, preferably **ephemeral** (new cloud nodes disappear when offline). Prefer restricted ACL/tag policy to allow only the Pi's port 22 once policy is established. Tailscale must allow the Railway worker to contact the Pi. A service with no auth cannot join the tailnet.
2. In **Railway → ram-drive-log-bridge → ram-pi-tailnet-probe → Variables**, add **`TS_AUTHKEY`** with the newly generated credential. Keep it private to Railway: **never paste it into ChatGPT, GitHub, issue comments or logs**.
3. Railway redeploys on variable change; watch service runtime log for `RAM_PI_TAILNET`. `pi_ssh_port_reachable` proves only **network/TCP access**, not SSH authentication or authority to run a command. `pi_ssh_port_unreachable` means Pi TCP/22 is not accepting the tunnel connection, perhaps firewall, Tailscale policy or sshd. `tailnet_auth_failed` means the service did not join.
4. The Pi's screenshot showed a **node key expiring in 3 days**. From the Tailscale **Machines** page, consider **Disable Key Expiry** for this *trusted, unattended Pi* so the connection will not unexpectedly stop. Do not disable expiry for untrusted machines. See Tailscale's official Key Expiry guidance.

**Only after this succeeds** should an SSH-authenticated maintenance mechanism be designed: it will need a dedicated restricted SSH identity with host-key verification, a Pi-to-Comma private route, explicit parked vehicle confirmation, and an allowlist for **diagnostic-only** commands. None of those is established by this connectivity probe. The Comma might be on a physically different LAN from the Sunnyslope Pi; no assumption of Pi-to-Comma reachability is made.

The read-only worker never holds a Comma key, initiates SSH authentication, writes a file, calls Comma APIs or changes car control. A successful GitHub CI or Railway deployment is not a vehicle installation.


## November 19 late-night live bridge status

- Railway service `ram-pi-tailnet-probe` was updated to support a Tailscale **API access token** (`tskey-api-`), which is not itself a device auth key. Its `mint_authkey.py` exchanges that token through the documented Tailscale REST API for a **single-use, ephemeral, 1-hour `tskey-auth-` key**. The temporary token is consumed in process memory without logging it.
- The API credential is stored privately as `TS_API_KEY` in this one Railway service, **not** in GitHub. `TS_AUTHKEY` remains an optional preferred direct device key if provided.
- **Verified live:** on pinned commit `b572cc82313ef9dfb408a58bf4a16e391124b8bf`, the worker logged `tailnet_node_registered` and `pi_ssh_port_reachable`. This proves userspace Tailscale and TCP/22 reach the existing Sunnyslope Pi.
- **Verified next gate:** on pinned commit `c3029b8f90ebdc9e058f9d2226b5f9601cba1445`, the one-time Tailscale SSH attempt running only `true` as Pi user `markswerner` logged `pi_ssh_auth_not_confirmed`. TCP/22 is still reachable, but **authenticated remote command execution is NOT established**. This could mean Pi Tailscale SSH is not enabled or policy requires an identity or confirmation; no remote command was executed.
- Therefore **no files on Pi or Comma were read or written**; no diagnostic installation or driving-control modification occurred. Do not infer that a Tailscale network connection is sufficient for SSH authorization.

### Security follow-up

The API access key was provided in a chat. **Treat it as exposed** and replace it with a narrowly scoped OAuth client or a dedicated restricted `tskey-auth-` credential stored only in Railway, then **revoke the original API access key** in the Tailscale Keys admin console. Revoking the API key now would not terminate the current registered node until the container restarts, but it would prevent automatic re-enrollment with the present stateless worker.

For unattended SSH maintenance, establish a dedicated restricted identity through one supported route: (a) enable Tailscale SSH on the Pi and grant the Railway node user-to-Pi policy, or (b) authorize a dedicated Railway SSH public key in the Pi's `markswerner` account and verify its host key. This requires a one-time **trusted administrative action on the Pi**; the current Tailscale API credential does not grant arbitrary host-shell permissions.
