// Microphone → 16 kHz mono, 20 ms frames (320 samples), posted to the page.
// Runs in the audio thread of the browser; the page does the rest.
class BixCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0; // position in the input stream, in input samples
    this.prev = 0;
    this.out = new Float32Array(320);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    // linear interpolation resampler (the browser's own filters already band-limit speech)
    let t = this.pos;
    while (t < ch.length) {
      const i = Math.floor(t), f = t - i;
      const a = i === 0 ? this.prev : ch[i - 1], b = ch[i];
      this.out[this.n++] = a + (b - a) * f;
      if (this.n === 320) {
        this.port.postMessage(this.out.slice(0));
        this.n = 0;
      }
      t += this.ratio;
    }
    this.pos = t - ch.length;
    this.prev = ch[ch.length - 1];
    return true;
  }
}

registerProcessor("bix-capture", BixCapture);
