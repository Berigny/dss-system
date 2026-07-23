/**
 * Audio Chat Surface v0.2
 * Vanilla JS, no framework.
 *
 * Two modes:
 *   1. WebRTC (default): two-state voice interface via gateway + LOAM backend.
 *   2. Web Speech (?mode=web-speech): browser handles STT/TTS;
 *      backend only runs LLM inference via POST /v1/voice/chat.
 */

const CONFIG = {
  // Gateway base URL. Priority: ?gateway=... > build-time default > empty.
  gatewayUrl: (() => {
    const fromQuery = new URLSearchParams(window.location.search).get('gateway');
    if (fromQuery) return fromQuery.replace(/\/$/, '');
    const fromBuild = typeof window !== 'undefined' && window.AUDIO_GATEWAY_URL ? window.AUDIO_GATEWAY_URL : '';
    if (fromBuild) return fromBuild.replace(/\/$/, '');
    return '';
  })(),
  // Backend chat API base URL. Priority: ?chat=... > build-time default > empty.
  chatUrl: (() => {
    const fromQuery = new URLSearchParams(window.location.search).get('chat');
    if (fromQuery) return fromQuery.replace(/\/$/, '');
    const fromBuild = typeof window !== 'undefined' && window.AUDIO_CHAT_API_URL ? window.AUDIO_CHAT_API_URL : '';
    if (fromBuild) return fromBuild.replace(/\/$/, '');
    return '';
  })(),
  mode: new URLSearchParams(window.location.search).get('mode') || 'webrtc',
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

// WebRTC mode state
let pc = null;
let localStream = null;
let sessionId = null;

// Web Speech mode state
let speechRecognition = null;
let isSpeaking = false;
let restartRecognitionAfterSpeak = false;
let webSpeechAvailable = false;

// --- UI state -------------------------------------------------------------

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
      els.status.textContent = CONFIG.mode === 'web-speech' ? 'Listening...' : 'Connected';
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

// --- Web Speech helpers ----------------------------------------------------

function getSpeechRecognition() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function speakText(text) {
  if (!window.speechSynthesis) {
    console.warn('speechSynthesis not available');
    return;
  }
  isSpeaking = true;
  stopWebSpeechRecognition();

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1;
  utterance.pitch = 1;
  utterance.onend = () => {
    isSpeaking = false;
    if (state === STATES.CONNECTED) {
      startWebSpeechRecognition();
    }
  };
  utterance.onerror = (err) => {
    console.warn('speechSynthesis error', err);
    isSpeaking = false;
    if (state === STATES.CONNECTED) {
      startWebSpeechRecognition();
    }
  };
  window.speechSynthesis.speak(utterance);
}

async function sendChatAndSpeak(text) {
  if (!CONFIG.chatUrl) {
    handleError('Chat API URL not configured. Use ?chat=https://...');
    return;
  }

  try {
    const response = await fetch(`${CONFIG.chatUrl}/v1/voice/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Chat error ${response.status}: ${body}`);
    }
    const data = await response.json();
    if (data.text) {
      speakText(data.text);
    }
  } catch (err) {
    console.error('Chat request failed', err);
    speakText('Sorry, I could not reach LOAM right now.');
  }
}

function startWebSpeechRecognition() {
  if (!speechRecognition || state !== STATES.CONNECTED || isSpeaking) return;

  try {
    speechRecognition.start();
  } catch (err) {
    // start() throws if already started; ignore.
    if (err.name !== 'InvalidStateError') {
      console.warn('SpeechRecognition start failed', err);
    }
  }
}

function stopWebSpeechRecognition() {
  if (!speechRecognition) return;
  try {
    speechRecognition.stop();
  } catch (_) {}
}

function createSpeechRecognition() {
  const SR = getSpeechRecognition();
  if (!SR) return null;

  const recognition = new SR();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-US';

  recognition.onresult = (event) => {
    const transcript = event.results[0][0].transcript.trim();
    if (transcript) {
      console.log('Heard:', transcript);
      sendChatAndSpeak(transcript);
    }
  };

  recognition.onerror = (event) => {
    // 'no-speech' and 'aborted' are common and usually benign.
    if (event.error !== 'no-speech' && event.error !== 'aborted') {
      console.warn('SpeechRecognition error', event.error);
    }
  };

  recognition.onend = () => {
    // Keep listening while connected and not speaking.
    if (state === STATES.CONNECTED && !isSpeaking) {
      startWebSpeechRecognition();
    }
  };

  return recognition;
}

// --- WebRTC helpers --------------------------------------------------------

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

async function startWebRTCConnection() {
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

async function stopWebRTCConnection() {
  if (state === STATES.DISCONNECTED) return;

  const sid = sessionId;
  cleanupWebRTC();
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

function cleanupWebRTC() {
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

// --- Web Speech mode lifecycle --------------------------------------------

async function startWebSpeechMode() {
  if (!webSpeechAvailable) {
    handleError('Web Speech API not supported in this browser.');
    return;
  }

  setState(STATES.CONNECTING);
  els.button.disabled = true;

  try {
    ensureAudioContext();

    if (!CONFIG.chatUrl) {
      throw new Error('Chat API URL not configured. Use ?chat=https://...');
    }

    // Create a fresh recognition instance (some browsers require this after stop).
    speechRecognition = createSpeechRecognition();

    setState(STATES.CONNECTED);
    playConnectTone();
    startWebSpeechRecognition();
  } catch (err) {
    handleError(err.message);
  }
}

async function stopWebSpeechMode() {
  if (state === STATES.DISCONNECTED) return;

  stopWebSpeechRecognition();
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  isSpeaking = false;
  speechRecognition = null;

  setState(STATES.DISCONNECTED);
  playDisconnectTone();
}

// --- Common lifecycle ------------------------------------------------------

async function startConnection() {
  if (state !== STATES.DISCONNECTED) return;

  if (CONFIG.mode === 'web-speech') {
    await startWebSpeechMode();
  } else {
    await startWebRTCConnection();
  }
}

async function stopConnection() {
  if (CONFIG.mode === 'web-speech') {
    await stopWebSpeechMode();
  } else {
    await stopWebRTCConnection();
  }
}

function cleanup() {
  cleanupWebRTC();
  stopWebSpeechRecognition();
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
  }
  isSpeaking = false;
  speechRecognition = null;
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

function init() {
  webSpeechAvailable = !!getSpeechRecognition() && !!window.speechSynthesis;

  if (CONFIG.mode === 'web-speech') {
    if (!webSpeechAvailable) {
      els.status.textContent = 'Web Speech not supported';
      els.button.disabled = true;
      return;
    }
    els.status.textContent = 'Tap to listen';
  }

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
}

init();
