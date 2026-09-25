/*
 * additions.cpp
 *
 * Hexagon programmer additions to jtag2updi.
 *
 * Hardware:
 *   Pin 9  (PB1, OC1A) → 500 Hz square wave → target PA6
 *   Pin 4  (PD4)        ← mode toggle button (active-low, internal pull-up)
 *   Pin 3  (PD3)        → soft ground for mode button (driven LOW)
 *   Pin 5  (PD5)        → mode LED anode (solid = PROGRAM, 1 Hz blink =
 * CALIBRATE) Pin 7  (PD7)        → soft ground for mode LED (driven LOW) Pin 0
 * (PD0, RX)    ↔ host UART / target UART TX in CALIBRATE mode Pin 1  (PD1, TX)
 * ↔ host UART / target UART RX in CALIBRATE mode Pin 6  (PD6)        → UPDI
 * data → target UPDI
 *
 * Two modes, toggled by the push button:
 *   CALIBRATE — Timer1 in CTC toggle mode drives 500 Hz on pin 9.
 *               Target UART monitor active. jtag2updi idle.
 *   PROGRAM   — Timer1 owned by jtag2updi (host/target timeouts, 0x04/CS12).
 *               500 Hz disabled. avrdude can connect normally.
 *
 * Root cause of the conflict: jtag2updi's startTimer() writes
 * TCCR1B = 0x04 (CS12, normal mode) every time it waits for a host byte,
 * destroying the CTC toggle setup. Mode switching gives Timer1 one owner.
 *
 * In CALIBRATE mode the hardware UART (pins 0/1) is repurposed for the target.
 * The USB bridge connects host ↔ pins 0/1 ↔ target transparently at 115200
 * baud. No CPU involvement — the hardware handles the passthrough completely.
 * Host UART baud is set to 115200 on entering CALIBRATE, 19200 on PROGRAM.
 */

#include "additions.h"

#include <avr/interrupt.h>
#include <avr/io.h>
#include <util/delay.h>

#include "JTAG2.h"
#include "sys.h"

// ---------------------------------------------------------------------------
// Mode state, button (pin 4 / PD4) and mode LED (pin 5 / PD5)
// ---------------------------------------------------------------------------
// Button: active-low with internal pull-up. LED on = CALIBRATE mode.

#define MODE_BTN_DDR DDRD
#define MODE_BTN_PORT PORTD
#define MODE_BTN_REG PIND
#define MODE_BTN_BIT (1 << PD4)

// Pin 3 (PD3) is driven LOW to act as the button's ground return.
// Wire: Pin 3 ── button ── Pin 4. No external GND connection needed.
#define MODE_BTN_GND_DDR DDRD
#define MODE_BTN_GND_PORT PORTD
#define MODE_BTN_GND_BIT (1 << PD3)

#define MODE_LED_DDR DDRD
#define MODE_LED_PORT PORTD
#define MODE_LED_BIT (1 << PD5)

// Pin 7 (PD7) is driven LOW to act as the LED's ground return.
// Wire: Pin 5 → LED → 470 Ω → Pin 7. No external GND connection needed.
#define MODE_LED_GND_DDR DDRD
#define MODE_LED_GND_PORT PORTD
#define MODE_LED_GND_BIT (1 << PD7)

HexProgrammer::Mode HexProgrammer::mode = HexProgrammer::PROGRAM;

void HexProgrammer::init_button_led(void) {
  MODE_BTN_GND_DDR |= MODE_BTN_GND_BIT;    // output
  MODE_BTN_GND_PORT &= ~MODE_BTN_GND_BIT;  // drive low (soft ground)
  MODE_BTN_DDR &= ~MODE_BTN_BIT;           // input
  MODE_BTN_PORT |= MODE_BTN_BIT;           // internal pull-up
  MODE_LED_GND_DDR |= MODE_LED_GND_BIT;    // output
  MODE_LED_GND_PORT &= ~MODE_LED_GND_BIT;  // drive low (soft ground)
  MODE_LED_DDR |= MODE_LED_BIT;            // output
  MODE_LED_PORT &= ~MODE_LED_BIT;          // off initially
}

// ---------------------------------------------------------------------------
// Status LED blink in CALIBRATE mode (~1 Hz via Timer2)
// ---------------------------------------------------------------------------
// Timer2 normal mode, prescaler 1024: overflow every 256 × (1024/16 MHz)
// = 16.384 ms. Toggle LED every 30 overflows ≈ 491 ms → ~1 Hz blink.
// In PROGRAM mode the LED is solid on — Timer2 is stopped.

#define BLINK_OVERFLOWS 30
static uint8_t blink_count = 0;

static void update_blink_led(void) {
  if (!(TIFR2 & (1 << TOV2))) return;
  TIFR2 = (1 << TOV2);  // clear by writing 1 (AVR convention)
  if (++blink_count >= BLINK_OVERFLOWS) {
    blink_count = 0;
    MODE_LED_PORT ^= MODE_LED_BIT;  // toggle
  }
}

void HexProgrammer::check_mode_button(void) {
  if (mode == CALIBRATE) update_blink_led();

  if (MODE_BTN_REG & MODE_BTN_BIT) return;  // idle high — not pressed

  _delay_ms(50);                            // debounce press
  if (MODE_BTN_REG & MODE_BTN_BIT) return;  // spurious

  if (mode == CALIBRATE) {
    enter_program_mode();
  } else {
    enter_calibrate_mode();
  }

  while (!(MODE_BTN_REG & MODE_BTN_BIT));  // wait for release
  _delay_ms(50);                           // debounce release
}

// ---------------------------------------------------------------------------
// 500 Hz square wave on pin 9 (PB1, Timer1 OC1A)
// ---------------------------------------------------------------------------
// CTC mode with toggle: f_out = F_CPU / (2 * prescaler * (OCR1A + 1))
// No prescaler:  500 Hz at 16 MHz → OCR1A = 15999
//                500 Hz at  8 MHz → OCR1A =  7999
//                500 Hz at 20 MHz → OCR1A = 19999

#if F_CPU == 16000000UL
#define OCR1A_500HZ 15999
#elif F_CPU == 8000000UL
#define OCR1A_500HZ 7999
#elif F_CPU == 20000000UL
#define OCR1A_500HZ 19999
#else
#error "F_CPU not 8/16/20 MHz — adjust OCR1A_500HZ for your clock speed"
#endif

void HexProgrammer::enter_calibrate_mode(void) {
  // Configure Timer1 for 500 Hz CTC toggle on OC1A (pin 9).
  // Must stop the timer first — jtag2updi may have left TCCR1B in normal mode.
  TCCR1B = 0;              // stop timer
  TCCR1A = (1 << COM1A0);  // toggle OC1A on compare match
  TCCR1B = (1 << WGM12);   // CTC mode, TOP = OCR1A
  OCR1A = OCR1A_500HZ;
  TCCR1B |= (1 << CS10);  // no prescaler — start timer
  DDRB |= (1 << DDB1);    // PB1 (pin 9) as output
  // Set host UART to 115200 so a terminal sees target output without
  // reconfiguring
  HOST_UBRR = baud_reg_val(115200UL);
  JTAG2::PARAM_BAUD_RATE_VAL = JTAG2::BAUD_115200;
  // Release pin 1 (TXD) so target TX can drive it cleanly to the USB bridge
  // RX. With TXEN set the MCU actively holds pin 1 high (idle mark state),
  // which fights the target's start bits and corrupts reception.
  HOST_UCSRB &= ~(1 << TXEN);
  DDRD &= ~(1 << PD1);  // high-Z — let target TX drive the line
  // Start Timer2 for ~1 Hz LED blink
  TCCR2A = 0;
  TCCR2B = (1 << CS22) | (1 << CS21) | (1 << CS20);  // prescaler 1024
  TCNT2 = 0;
  TIFR2 = (1 << TOV2);  // clear any pending overflow
  blink_count = 0;
  mode = CALIBRATE;
  MODE_LED_PORT |= MODE_LED_BIT;  // start blink with LED on
}

void HexProgrammer::enter_program_mode(void) {
  // Yield Timer1 back to jtag2updi.
  // jtag2updi uses normal mode, CS12 (prescaler 256), with
  // OCR1A = HOST_TIMEOUT and OCR1B = TARGET_TIMEOUT for ~250 ms / ~100 ms.
  TCCR1B = 0;  // stop timer
  TCCR1A = 0;  // clear COM1A0 — OC1A no longer timer-driven
  OCR1A = (uint16_t)(HOST_TIMEOUT);  // restore jtag2updi timeout values
  OCR1B = (uint16_t)(TARGET_TIMEOUT);
  // TCCR1B will be set to TIMER_ON (0x04) by startTimer() on demand
  // Reset host UART to 19200 — avrdude always opens at this baud
  HOST_UBRR = baud_reg_val(19200UL);
  JTAG2::PARAM_BAUD_RATE_VAL = JTAG2::BAUD_19200;
  // Restore pin 1 (TXD) — UART hardware takes control of the pin when TXEN is
  // set
  HOST_UCSRB |= (1 << TXEN);
  // Stop blink timer; LED solid on in PROGRAM mode
  TCCR2B = 0;
  mode = PROGRAM;
  MODE_LED_PORT |= MODE_LED_BIT;  // solid on
}

// ---------------------------------------------------------------------------
// Target UART — hardware passthrough via pins 0/1 in CALIBRATE mode
// ---------------------------------------------------------------------------
// The Uno's hardware UART is already bridged to the host by the USB chip.
// In CALIBRATE mode the baud is set to 115200, making the path
// host ↔ USB bridge ↔ pins 0/1 ↔ target fully transparent with zero CPU
// overhead. enter_calibrate_mode() and enter_program_mode() handle the
// baud switching; no further code is needed here.
