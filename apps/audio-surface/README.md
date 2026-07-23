# Audio Chat Surface v0.1

Browser-based, single-channel voice interface to LOAM.

## Scope

- Vanilla JS, no framework.
- One main button: tap to connect, tap again to disconnect.
- WebRTC audio capture + Opus 48 kHz / 20 ms.
- CSS-only waveform and audio tones for state feedback.
- Auto-reset error feedback; no modals, no retry button.

## Run locally

```bash
cd apps/audio-surface
python3 -m http.server 8080
```

Open `http://localhost:8080?gateway=https://your-gateway.example.com`.

## Gateway contract expected

- `POST {gatewayUrl}/session` with JSON body `{ offer: <SDP> }`
  - Returns `{ session_id: string, answer: <SDP> }`
- `DELETE {gatewayUrl}/session/{session_id}`

## Notes

- Part of the `dss-system` monorepo.
- Deployed as a static app (Vercel or equivalent).
