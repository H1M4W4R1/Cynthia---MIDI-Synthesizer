/*
 * MIDI UART Pass-Through for Raspberry Pi Pico
 *
 * UART0:  GP0 = TX, GP1 = RX @ 31250 baud (MIDI)
 *
 * Both USB Serial and UART0 RX are forwarded to UART0 TX.
 * USB input is parsed and validated — System Reset (0xFF) is silently dropped.
 * UART RX is echoed back to USB Serial for monitoring.
 * VLSI chip is reset on startup via RST_PIN.
 */

#define MIDI_BAUD 31250
#define RST_PIN   12    // GP15 — adjust to your wiring

// ---------------------------------------------------------------------------
// MIDI parser — used for USB input only (UART is a trusted hardware source)
// ---------------------------------------------------------------------------

int midiMessageLength(uint8_t status) {
  if (status == 0xFF) return 1; // System Reset — handled as special case
  if (status == 0xF0) return -1; // SysEx — variable
  if (status >= 0xF0) {
    switch (status) {
      case 0xF1: return 2;  // MIDI Time Code
      case 0xF2: return 3;  // Song Position Pointer
      case 0xF3: return 2;  // Song Select
      case 0xF6: return 1;  // Tune Request
      case 0xF7: return 1;  // End of SysEx
      case 0xF8: return 1;  // Timing Clock
      case 0xFA: return 1;  // Start
      case 0xFB: return 1;  // Continue
      case 0xFC: return 1;  // Stop
      case 0xFE: return 1;  // Active Sensing
      default:   return 0;  // Unknown — discard
    }
  }

  switch (status & 0xF0) {
    case 0x80: return 3;  // Note Off
    case 0x90: return 3;  // Note On
    case 0xA0: return 3;  // Poly Aftertouch
    case 0xB0: return 3;  // Control Change
    case 0xC0: return 2;  // Program Change
    case 0xD0: return 2;  // Channel Aftertouch
    case 0xE0: return 3;  // Pitch Bend
    default:   return 0;  // Unknown — discard
  }
}

// Parser state for USB input
static uint8_t usbBuf[3];
static int     usbBufLen   = 0;
static int     usbExpected = 0;
static bool    usbInSysEx  = false;

void processUsbByte(uint8_t b) {
  // ── Real-Time messages ─────────────────────────────────────────────────────
  // Single byte, can appear anywhere. System Reset (0xFF) is dropped.
  if (b >= 0xF8) {
    if (b != 0xFF) {
      Serial1.write(b);
      Serial.write(b);    // Echo to USB
    }
    return;
  }

  // ── End of SysEx ──────────────────────────────────────────────────────────
  if (b == 0xF7) {
    if (usbInSysEx) {
      Serial1.write(b);
      Serial.write(b);
      usbInSysEx = false;
    }
    usbBufLen   = 0;
    usbExpected = 0;
    return;
  }

  // ── Start of SysEx ────────────────────────────────────────────────────────
  if (b == 0xF0) {
    usbInSysEx  = true;
    usbBufLen   = 0;
    usbExpected = 0;
    Serial1.write(b);
    Serial.write(b);
    return;
  }

  // ── SysEx data bytes ──────────────────────────────────────────────────────
  if (usbInSysEx) {
    if (b < 0x80) {
      Serial1.write(b);
      Serial.write(b);
    }
    return;
  }

  // ── Status byte ───────────────────────────────────────────────────────────
  if (b >= 0x80) {
    usbBufLen   = 0;
    usbExpected = midiMessageLength(b);
    if (usbExpected == 0) return;

    usbBuf[usbBufLen++] = b;

    if (usbExpected == 1) {
      Serial1.write(b);
      Serial.write(b);
      usbBufLen   = 0;
      usbExpected = 0;
    }
    return;
  }

  // ── Data byte ─────────────────────────────────────────────────────────────
  if (usbExpected == 0) return;   // Orphaned data — discard

  usbBuf[usbBufLen++] = b;

  if (usbBufLen >= usbExpected) {
    Serial1.write(usbBuf, usbBufLen);
    Serial.write(usbBuf, usbBufLen);   // Echo complete message to USB
    usbBufLen   = 0;
    usbExpected = 0;
  }
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(MIDI_BAUD);  // USB Serial

  Serial1.setTX(0);         // GP0
  Serial1.setRX(1);         // GP1
  Serial1.begin(MIDI_BAUD);

  // VLSI reset — hold LOW briefly then release HIGH to operate
  pinMode(RST_PIN, OUTPUT);
  digitalWrite(RST_PIN, LOW);
  delay(10);
  digitalWrite(RST_PIN, HIGH);
  delay(10);
}

void loop() {
  // UART RX → UART TX (trusted hardware source, pass straight through)
  // Also echo to USB so the host can monitor incoming MIDI traffic
  while (Serial1.available()) {
    uint8_t b = Serial1.read();
    Serial1.write(b);
    Serial.write(b);
  }

  // USB → UART TX (untrusted, run through MIDI parser)
  // Validated bytes are echoed back to USB inside processUsbByte()
  while (Serial.available()) {
    processUsbByte((uint8_t)Serial.read());
  }
}