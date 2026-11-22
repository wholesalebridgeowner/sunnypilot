# Shared Uconnect dashboard

This optional service displays the RamPilot readings on the Magic Box browser. The advisor supplies vehicle speed, the enabled cruise setpoint, lateral control activity, torque-controller saturation and lane-change phase. The Qt screen supplies the current drive's heartbeat, blind-spot feed configuration at startup, the truck's speed-limit sign and validated diesel readings. The screen and advisor use their existing subscriptions. Dashboard clients share one cached sample.

The sources each send at most two nonblocking local datagrams per second to separate endpoints in an owner-only directory. Packets carry original event timestamps and a monotonic send time. Only current-drive values within their individual age limits enter the displayed sample. Loss of the drive heartbeat or vehicle speed clears the whole dashboard. Other missing inputs clear their own readings. A source packet expires after 1.25 seconds. The service checks at 2 Hz. The browser also clears a frozen connection and falls back to authenticated polling if needed. DPF regen stays **Unknown** until its producer validation switch and fresh validity flag are both true.

## Starting the service

The default install leaves the dashboard off. A separately approved setup needs the relay-enabled advisor, its installed launcher, and a screen built with the shared-feed patch. Once the vehicle is parked and offroad, run the foreground service from the approved checkout with the Comma's private Wi-Fi IPv4 address.

```sh
bash rampilot/dash/start.sh <comma-private-ip>
```

The service checks the advisor installation before binding its sockets or creating pairing credentials. Its first start creates an owner-only token in `/data/ram-diag/dash/token`. Pair the browser while parked using `http://<comma-private-ip>:8765/#token=<token>`. The page clears the fragment and uses tab-scoped storage. Use a trusted private network. HTTP traffic, including pairing credentials, can be observed by peers with access to that traffic. Ctrl-C stops the service.

The server limits streams to four WebSocket clients. All clients share the same sampling and resource guards. The service stops after two successive ten-second windows over 15% of one CPU core or one anonymous RSS reading over 150 MiB. A missing producer or busy receiver can drop packets. Successful browser requests do not refresh the source timestamps.

## Offline checks

On Linux with aiohttp available, run the dashboard tests from the checkout root.

```sh
python3 -m unittest discover -s rampilot/dash -p 'test_*.py'
```
