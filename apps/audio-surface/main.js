/**
 * Audio Chat Surface v0.1
 * Vanilla JS, no framework.
 * Two-state voice interface to LOAM via WebRTC (Opus).
 */

const CONFIG = {
  // Gateway base URL. Override with ?gateway=https://...
  gatewayUrl: (() => {
    const fromQuery = new URLSearchParams(window.location.search).get('gateway');
    if (fromQuery) return fromQuery.replace(/\/$/, '');
    return '';
  })(),
  iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  opusMimeType: 'audio/opus',
};

const STATES = {
  DISCONNECTED: 'disconnected',
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
};

const els = {
  button: document.getElementById('audio-button'),
  icon: document.getElementById('audio-icon'),
  status: document.getElementById('status-label'),
  remoteAudio: document.getElementById('remote-audio'),
};

let audioCtx = null;
let state = STATES.DISCONNECTED;
let pc = null;
let localStream = null;
let sessionId = null;

// --- UI state ---------------------------------------------------------------

function setState(newState) {
  state = newState;
  document.body.className = newState;
  els.button.setAttribute('data-state', newState);
  els.button.disabled = false;

  switch (newState) {
    case STATES.DISCONNECTED:
      els.icon.textContent = '🎤';
      els.status.textContent = 'Tap to connect';
      break;
    case STATES.CONNECTING:
      els.icon.textContent = '⋯';
      els.status.textContent = 'Connecting...';
      break;
    case STATES.CONNECTED:
      els.icon.textContent = '●';
      els.status.textContent = 'Connected';
      break;
  }
}

// --- Audio feedback ---------------------------------------------------------

function ensureAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
}

function playTone({ freq = 440, duration = 0.12, type = 'sine', peak = 0.08 } = {}) {
  try {
    ensureAudioContext();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
    osc.connect(gain);
    gain.connect(audioCtx.destination);

    const now = audioCtx.currentTime;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(peak, now + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

    osc.start(now);
    osc.stop(now + duration);
  } catch (e) {
    console.warn('Tone playback failed', e);
  }
}

function playConnectTone() {
  playTone({ freq: 880, duration: 0.15, type: 'sine', peak: 0.1 });
}

function playDisconnectTone() {
  playTone({ freq: 440, duration: 0.15, type: 'sine', peak: 0.08 });
}

// --- SDP helpers ------------------------------------------------------------

function preferOpus(sdp) {
  const mLine = /m=audio.*\r\n/.exec(sdp);
  if (!mLine) return sdp;

  const payloadMatch = sdp.match(/a=rtpmap:(\d+) opus\/48000\/2/i);
  if (!payloadMatch) return sdp;

  const opusPayload = payloadMatch[1];

  // Reorder codecs on the m=audio line so Opus is first.
  return sdp.replace(/(m=audio \d+ [A-Z\/]+)( .*)/, (match, prefix, rest) => {
    const codecs = rest.trim().split(' ').filter(Boolean);
    const withoutOpus = codecs.filter((c) => c !== opusPayload);
    return `${prefix} ${opusPayload} ${withoutOpus.join(' ')}`;
  });
}

// --- WebRTC -----------------------------------------------------------------

function waitForIceGathering(peerConnection) {
  return new Promise((resolve) => {
    if (peerConnection.iceGatheringState === 'complete') {
      resolve();
      return;
    }

    const onStateChange = () => {
      if (peerConnection.iceGatheringState === 'complete') {
        peerConnection.removeEventListener('icegatheringstatechange', onStateChange);
        clearTimeout(timeout);
        resolve();
      }
    };

    // Cap ICE gathering at 3 seconds so low-bandwidth users are not blocked.
    const timeout = setTimeout(() => {
      peerConnection.removeEventListener('icegatheringstatechange', onStateChange);
      resolve();
    }, 3000);

    peerConnection.addEventListener('icegatheringstatechange', onStateChange);
  });
}

async function startConnection() {
  if (state !== STATES.DISCONNECTED) return;

  setState(STATES.CONNECTING);
  els.button.disabled = true;

  try {
    ensureAudioContext();

    pc = new RTCPeerConnection({ iceServers: CONFIG.iceServers });

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === 'connected') {
        if (state !== STATES.CONNECTED) {
          setState(STATES.CONNECTED);
          playConnectTone();
        }
      } else if (['disconnected', 'failed', 'closed'].includes(pc.connectionState)) {
        handleError('Connection ended');
      }
    };

    pc.ontrack = (event) => {
      if (els.remoteAudio && event.streams && event.streams[0]) {
        els.remoteAudio.srcObject = event.streams[0];
      }
    };

    localStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 48000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    const [audioTrack] = localStream.getAudioTracks();
    pc.addTrack(audioTrack, localStream);

    const offer = await pc.createOffer();
    const offerWithOpus = preferOpus(offer.sdp);
    await pc.setLocalDescription({ type: offer.type, sdp: offerWithOpus });

    await waitForIceGathering(pc);

    if (!CONFIG.gatewayUrl) {
      throw new Error('Gateway URL not configured. Use ?gateway=https://...');
    }

    const response = await fetch(`${CONFIG.gatewayUrl}/session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ offer: pc.localDescription }),
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Gateway error ${response.status}: ${body}`);
    }

    const data = await response.json();
    sessionId = data.session_id;

    if (!data.answer) {
      throw new Error('Gateway response missing answer SDP');
    }

    await pc.setRemoteDescription(new RTCSessionDescription(data.answer));
  } catch (err) {
    handleError(err.message);
  }
}

async function stopConnection() {
  if (state === STATES.DISCONNECTED) return;

  const sid = sessionId;
  cleanup();
  setState(STATES.DISCONNECTED);
  playDisconnectTone();

  if (sid && CONFIG.gatewayUrl) {
    try {
      await fetch(`${CONFIG.gatewayUrl}/session/${sid}`, { method: 'DELETE' });
    } catch (e) {
      console.warn('Session cleanup request failed', e);
    }
  }
}

function cleanup() {
  if (pc) {
    try { pc.close(); } catch (_) {}
    pc = null;
  }
  if (localStream) {
    localStream.getTracks().forEach((track) => track.stop());
    localStream = null;
  }
  sessionId = null;
  if (els.remoteAudio) {
    els.remoteAudio.srcObject = null;
  }
}

function handleError(message) {
  console.error('Audio Chat error:', message);
  cleanup();
  setState(STATES.DISCONNECTED);
  els.status.textContent = 'Error';
  els.button.classList.add('error');

  setTimeout(() => {
    els.button.classList.remove('error');
    if (state === STATES.DISCONNECTED) {
      els.status.textContent = 'Tap to connect';
    }
  }, 1000);
}

// --- Input handling ---------------------------------------------------------

els.button.addEventListener('click', () => {
  if (state === STATES.DISCONNECTED) {
    startConnection();
  } else {
    stopConnection();
  }
});

// Resume AudioContext on first user gesture for autoplay policy compliance.
els.button.addEventListener('pointerdown', () => {
  ensureAudioContext();
});

setState(STATES.DISCONNECTED);
