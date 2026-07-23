/**
 * Voice Gateway v0.1
 * Stateless WebRTC <-> LOAM WebSocket proxy.
 *
 * Architecture:
 *   Browser (WebRTC opus)  <->  Gateway  <->  LOAM core (WS /v1/voice/stream/{id})
 *
 * The gateway performs no audio decode/encode. It forwards opus payloads
 * extracted from incoming RTP packets to LOAM core, and wraps opus frames
 * received from LOAM core into RTP packets for the return WebRTC stream.
 *
 * A --mock-core mode is provided so the gateway can be tested standalone.
 */

const express = require('express');
const http = require('http');
const { WebSocketServer } = require('ws');
const {
  RTCPeerConnection,
  MediaStreamTrack,
  RTCRtpCodecParameters,
  RtpPacket,
} = require('werift');

const PORT = parseInt(process.env.PORT || '3000', 10);
const LOAM_VOICE_API = (process.env.LOAM_VOICE_API || 'http://localhost:8000').replace(/\/$/, '');
const MOCK_CORE = process.env.MOCK_CORE === 'true' || process.env.MOCK_CORE === '1';

// In-memory handles for active sessions only. No persistent state.
const sessions = new Map();

const app = express();
app.use(express.json());

// --- Helpers ----------------------------------------------------------------

function generateSessionId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

function createPeerConnection() {
  return new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
    codecs: {
      audio: [
        new RTCRtpCodecParameters({
          mimeType: 'audio/opus',
          clockRate: 48000,
          channels: 2,
          payloadType: 111,
        }),
      ],
      video: [],
    },
  });
}

function cleanupSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return;

  try { session.pc.close(); } catch (_) {}
  if (session.loamWs) {
    try { session.loamWs.close(); } catch (_) {}
  }
  if (session.heartbeatInterval) {
    clearInterval(session.heartbeatInterval);
  }
  sessions.delete(sessionId);
  console.log(`Session ${sessionId} cleaned up`);
}

// --- Mock core mode ---------------------------------------------------------

function startMockCore(session) {
  console.log(`Mock core active for session ${session.id}`);
  // Echo: send back whatever we receive.
  // In a real LOAM core this is replaced by WS /v1/voice/stream/{id}.
  session.mockEcho = true;
}

// --- LOAM core WebSocket connection -----------------------------------------

function connectLoamCore(session) {
  if (MOCK_CORE) {
    startMockCore(session);
    return;
  }

  const wsUrl = `${LOAM_VOICE_API.replace(/^http/, 'ws')}/v1/voice/stream/${session.id}`;
  const WebSocket = require('ws');
  const ws = new WebSocket(wsUrl);

  ws.on('open', () => {
    console.log(`Connected to LOAM core for session ${session.id}`);
  });

  ws.on('message', (data) => {
    // data is an opus frame (or opus RTP payload) from LOAM core.
    forwardOpusToBrowser(session, data);
  });

  ws.on('error', (err) => {
    console.error(`LOAM core WS error for session ${session.id}:`, err.message);
  });

  ws.on('close', () => {
    console.log(`LOAM core WS closed for session ${session.id}`);
    cleanupSession(session.id);
  });

  session.loamWs = ws;

  // Heartbeat to keep session alive.
  session.heartbeatInterval = setInterval(() => {
    if (ws.readyState === ws.OPEN) {
      ws.send(JSON.stringify({ type: 'heartbeat' }));
    }
  }, 5000);
}

function forwardOpusToBrowser(session, opusData) {
  if (!session.outgoingTrack) return;

  // Build an RTP packet around the opus frame.
  const seq = session.outSeq++;
  const timestamp = session.outTimestamp;
  // Advance timestamp by 20 ms @ 48 kHz = 960 samples per channel, stereo = 960.
  session.outTimestamp += 960;

  const payloadType = 111;
  const ssrc = session.outSsrc;

  const rtp = new RtpPacket(
    {
      version: 2,
      padding: false,
      extension: false,
      marker: false,
      payloadType,
      sequenceNumber: seq,
      timestamp,
      ssrc,
      csrc: [],
      extensionProfile: 0,
      extensions: [],
    },
    Buffer.from(opusData),
  );

  session.outgoingTrack.writeRtp(rtp);
}

function forwardOpusToCore(session, opusPayload) {
  if (MOCK_CORE && session.mockEcho) {
    // Echo back after a short delay to simulate latency.
    setTimeout(() => forwardOpusToBrowser(session, opusPayload), 100);
    return;
  }

  if (!session.loamWs) return;
  if (session.loamWs.readyState === session.loamWs.OPEN) {
    session.loamWs.send(opusPayload);
  }
}

// --- HTTP endpoints ---------------------------------------------------------

app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    loam_voice_api: LOAM_VOICE_API,
    mock_core: MOCK_CORE,
    active_sessions: sessions.size,
  });
});

app.post('/session', async (req, res) => {
  try {
    const { offer } = req.body;
    if (!offer || typeof offer !== 'object' || !offer.sdp) {
      return res.status(400).json({ error: 'Missing offer SDP' });
    }

    const sessionId = generateSessionId();
    const pc = createPeerConnection();

    const outgoingTrack = new MediaStreamTrack({ kind: 'audio' });
    const transceiver = pc.addTransceiver(outgoingTrack, { direction: 'sendrecv' });

    const session = {
      id: sessionId,
      pc,
      outgoingTrack,
      transceiver,
      loamWs: null,
      heartbeatInterval: null,
      mockEcho: false,
      outSeq: Math.floor(Math.random() * 65535),
      outTimestamp: 0,
      outSsrc: Math.floor(Math.random() * 0xffffffff),
      inSsrc: null,
    };

    pc.onconnectionstatechange = () => {
      console.log(`Session ${sessionId} connection state: ${pc.connectionState}`);
      if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) {
        cleanupSession(sessionId);
      }
    };

    transceiver.onTrack.subscribe((track) => {
      console.log(`Session ${sessionId} received audio track`);
      session.inSsrc = track.ssrc;

      track.onReceiveRtp.subscribe((rtpPacket) => {
        // Forward opus payload (RTP payload) to LOAM core.
        forwardOpusToCore(session, rtpPacket.payload);
      });
    });

    await pc.setRemoteDescription(offer);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);

    sessions.set(sessionId, session);

    // Connect to LOAM core now so the return path is ready.
    connectLoamCore(session);

    res.json({ session_id: sessionId, answer: pc.localDescription });
  } catch (err) {
    console.error('Session creation failed:', err);
    res.status(500).json({ error: err.message });
  }
});

app.delete('/session/:id', (req, res) => {
  const sessionId = req.params.id;
  cleanupSession(sessionId);
  res.status(204).send();
});

// --- WebSocket endpoint (browser data channel / control) --------------------

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/control/:id' });

wss.on('connection', (ws, req) => {
  const sessionId = req.url.split('/').pop();
  const session = sessions.get(sessionId);
  if (!session) {
    ws.close(1008, 'Unknown session');
    return;
  }

  console.log(`Control WS connected for session ${sessionId}`);

  ws.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString());
      if (msg.type === 'heartbeat') {
        ws.send(JSON.stringify({ type: 'heartbeat' }));
      }
    } catch (_) {
      // Ignore non-JSON control messages.
    }
  });

  ws.on('close', () => {
    console.log(`Control WS disconnected for session ${sessionId}`);
  });
});

// --- Startup ----------------------------------------------------------------

server.listen(PORT, () => {
  console.log(`Voice gateway listening on port ${PORT}`);
  console.log(`LOAM voice API: ${LOAM_VOICE_API}`);
  console.log(`Mock core mode: ${MOCK_CORE}`);
});
