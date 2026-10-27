/**
 * recorder.js — Audio recording module for Agastya Assist.
 *
 * Captures microphone audio using getUserMedia with AudioWorklet
 * (inline worklet via Blob URL) or ScriptProcessor fallback.
 * Resamples to 16 kHz mono 16-bit PCM, encodes RIFF/WAV,
 * and returns base64.
 * Enforces a maximum recording duration of 60 seconds.
 */

/**
 * Resample Float32 audio samples to target sample rate (default 16000 Hz)
 * using linear interpolation.
 */
export function resampleTo16k(audioBuffer, sourceSampleRate, targetSampleRate = 16000) {
  if (sourceSampleRate === targetSampleRate) {
    return audioBuffer;
  }
  const ratio = sourceSampleRate / targetSampleRate;
  const newLength = Math.round(audioBuffer.length / ratio);
  const result = new Float32Array(newLength);

  for (let i = 0; i < newLength; i++) {
    const origin = i * ratio;
    const index1 = Math.floor(origin);
    const index2 = Math.min(index1 + 1, audioBuffer.length - 1);
    const frac = origin - index1;
    result[i] = audioBuffer[index1] * (1 - frac) + audioBuffer[index2] * frac;
  }
  return result;
}

/**
 * Convert normalized Float32 samples (-1.0 to 1.0) to 16-bit signed PCM.
 */
export function floatTo16BitPCM(floatArray) {
  const pcm = new Int16Array(floatArray.length);
  for (let i = 0; i < floatArray.length; i++) {
    const s = Math.max(-1, Math.min(1, floatArray[i]));
    pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm;
}

/**
 * Build a valid 44-byte RIFF/WAV header around 16-bit mono PCM samples.
 */
export function encodeWAV(pcmSamples, sampleRate = 16000) {
  const numChannels = 1;
  const bytesPerSample = 2; // 16-bit PCM
  const dataByteLength = pcmSamples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataByteLength);
  const view = new DataView(buffer);

  function writeString(offset, string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  // RIFF chunk descriptor
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + dataByteLength, true);
  writeString(8, 'WAVE');

  // fmt subchunk
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true); // subchunk1 size (16 for PCM)
  view.setUint16(20, 1, true); // AudioFormat (1 = PCM)
  view.setUint16(22, numChannels, true); // NumChannels
  view.setUint32(24, sampleRate, true); // SampleRate
  view.setUint32(28, sampleRate * numChannels * bytesPerSample, true); // ByteRate
  view.setUint16(32, numChannels * bytesPerSample, true); // BlockAlign
  view.setUint16(34, 16, true); // BitsPerSample (16 bits)

  // data subchunk
  writeString(36, 'data');
  view.setUint32(40, dataByteLength, true);

  // PCM sample payload
  let offset = 44;
  for (let i = 0; i < pcmSamples.length; i++, offset += 2) {
    view.setInt16(offset, pcmSamples[i], true);
  }

  return buffer;
}

/**
 * Convert an ArrayBuffer to a base64 encoded string.
 */
export function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  const len = bytes.byteLength;
  const chunkSize = 8192;
  for (let i = 0; i < len; i += chunkSize) {
    const chunk = bytes.subarray(i, Math.min(i + chunkSize, len));
    binary += String.fromCharCode.apply(null, chunk);
  }
  return btoa(binary);
}

/**
 * AudioRecorder class managing mic capture, AudioWorklet/ScriptProcessor,
 * max 60s timeout, and encoding to base64 WAV.
 */
export class AudioRecorder {
  constructor(options = {}) {
    this.targetSampleRate = options.targetSampleRate || 16000;
    this.maxDurationSeconds = options.maxDurationSeconds || 60;
    this.onTimeout = options.onTimeout || null;
    this.onStateChange = options.onStateChange || null;

    this.stream = null;
    this.audioContext = null;
    this.workletNode = null;
    this.scriptNode = null;
    this.sourceNode = null;
    this.chunks = [];
    this._isRecording = false;
    this._timeoutId = null;
    this._startTime = 0;
  }

  isRecording() {
    return this._isRecording;
  }

  async start() {
    if (this._isRecording) {
      return;
    }

    // 1. Request microphone stream
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: { ideal: 1 },
        sampleRate: { ideal: this.targetSampleRate },
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    // 2. Initialize AudioContext
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    try {
      this.audioContext = new AudioCtx({ sampleRate: this.targetSampleRate });
    } catch {
      this.audioContext = new AudioCtx();
    }

    if (this.audioContext.state === 'suspended') {
      await this.audioContext.resume();
    }

    this.sourceNode = this.audioContext.createMediaStreamSource(this.stream);
    this.chunks = [];
    this._isRecording = true;
    this._startTime = Date.now();

    // 3. Setup AudioWorklet or ScriptProcessor fallback
    let workletConfigured = false;
    if (this.audioContext.audioWorklet) {
      try {
        const workletCode = `
          class RecorderProcessor extends AudioWorkletProcessor {
            constructor() {
              super();
              this._active = true;
              this.port.onmessage = (e) => {
                if (e.data === 'stop') this._active = false;
              };
            }
            process(inputs) {
              if (!this._active) return true;
              const input = inputs[0];
              if (input && input.length > 0) {
                const channel = input[0];
                const copy = new Float32Array(channel.length);
                copy.set(channel);
                this.port.postMessage(copy);
              }
              return true;
            }
          }
          registerProcessor('agastya-recorder-processor', RecorderProcessor);
        `;
        const blob = new Blob([workletCode], { type: 'application/javascript' });
        const blobUrl = URL.createObjectURL(blob);
        await this.audioContext.audioWorklet.addModule(blobUrl);
        URL.revokeObjectURL(blobUrl);

        this.workletNode = new AudioWorkletNode(this.audioContext, 'agastya-recorder-processor');
        this.workletNode.port.onmessage = (e) => {
          if (this._isRecording && e.data) {
            this.chunks.push(e.data);
          }
        };

        this.sourceNode.connect(this.workletNode);
        // Connect to dummy muted gain to keep processing active in browsers that require a destination
        const dummyGain = this.audioContext.createGain();
        dummyGain.gain.value = 0;
        this.workletNode.connect(dummyGain);
        dummyGain.connect(this.audioContext.destination);

        workletConfigured = true;
      } catch (err) {
        console.warn('AudioWorklet failed, using ScriptProcessor fallback:', err);
        workletConfigured = false;
      }
    }

    if (!workletConfigured) {
      const bufferSize = 4096;
      this.scriptNode = this.audioContext.createScriptProcessor(bufferSize, 1, 1);
      this.scriptNode.onaudioprocess = (e) => {
        if (!this._isRecording) return;
        const channelData = e.inputBuffer.getChannelData(0);
        const copy = new Float32Array(channelData.length);
        copy.set(channelData);
        this.chunks.push(copy);
      };
      this.sourceNode.connect(this.scriptNode);
      this.scriptNode.connect(this.audioContext.destination);
    }

    // 4. Set max duration guard (60s)
    this._timeoutId = setTimeout(() => {
      if (this._isRecording && typeof this.onTimeout === 'function') {
        this.onTimeout();
      }
    }, this.maxDurationSeconds * 1000);

    if (typeof this.onStateChange === 'function') {
      this.onStateChange('recording');
    }
  }

  async stop() {
    if (!this._isRecording) {
      return null;
    }
    this._isRecording = false;

    if (this._timeoutId) {
      clearTimeout(this._timeoutId);
      this._timeoutId = null;
    }

    if (typeof this.onStateChange === 'function') {
      this.onStateChange('processing');
    }

    // Stop worklet/script nodes
    if (this.workletNode) {
      try {
        this.workletNode.port.postMessage('stop');
        this.workletNode.disconnect();
      } catch {
        // ignore disconnect errors
      }
    }
    if (this.scriptNode) {
      try {
        this.scriptNode.disconnect();
      } catch {
        // ignore
      }
    }
    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch {
        // ignore
      }
    }

    // Stop mic stream tracks
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }

    const sourceRate = this.audioContext ? this.audioContext.sampleRate : this.targetSampleRate;

    // Close AudioContext
    if (this.audioContext && this.audioContext.state !== 'closed') {
      try {
        await this.audioContext.close();
      } catch {
        // ignore
      }
      this.audioContext = null;
    }

    // Merge Float32 chunks
    let totalLength = 0;
    for (const chunk of this.chunks) {
      totalLength += chunk.length;
    }

    if (totalLength === 0) {
      if (typeof this.onStateChange === 'function') {
        this.onStateChange('idle');
      }
      return null;
    }

    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of this.chunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    this.chunks = [];

    // Resample to 16 kHz
    const resampled = resampleTo16k(merged, sourceRate, this.targetSampleRate);

    // Convert to 16-bit PCM
    const pcmSamples = floatTo16BitPCM(resampled);

    // Encode RIFF/WAV
    const wavBuffer = encodeWAV(pcmSamples, this.targetSampleRate);

    // Encode base64
    const audio_b64 = arrayBufferToBase64(wavBuffer);
    const duration = pcmSamples.length / this.targetSampleRate;

    if (typeof this.onStateChange === 'function') {
      this.onStateChange('idle');
    }

    return {
      audio_b64,
      mime: 'audio/wav',
      duration,
    };
  }

  cancel() {
    this._isRecording = false;
    if (this._timeoutId) {
      clearTimeout(this._timeoutId);
      this._timeoutId = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
      this.stream = null;
    }
    if (this.audioContext && this.audioContext.state !== 'closed') {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    this.chunks = [];
    if (typeof this.onStateChange === 'function') {
      this.onStateChange('idle');
    }
  }
}

export default AudioRecorder;
