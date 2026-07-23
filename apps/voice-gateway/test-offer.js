/**
 * Quick smoke test for voice gateway offer/answer.
 */

const { RTCPeerConnection, MediaStreamTrack } = require('werift');

async function main() {
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
  });

  const track = new MediaStreamTrack({ kind: 'audio' });
  pc.addTransceiver(track, { direction: 'sendrecv' });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // Wait briefly for ICE gathering
  await new Promise((r) => setTimeout(r, 500));

  const response = await fetch('http://localhost:3000/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ offer: pc.localDescription }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Gateway returned ${response.status}: ${text}`);
  }

  const data = await response.json();
  console.log('Session ID:', data.session_id);
  console.log('Answer type:', data.answer?.type);
  console.log('Answer has SDP:', !!data.answer?.sdp);

  if (!data.session_id || !data.answer?.sdp) {
    throw new Error('Gateway response missing session_id or answer SDP');
  }

  await pc.setRemoteDescription(data.answer);
  console.log('Remote description set. Test passed.');

  pc.close();

  // Clean up session
  await fetch(`http://localhost:3000/session/${data.session_id}`, { method: 'DELETE' });
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
