/*
 * additions.h
 *
 * Hexagon programmer additions to jtag2updi.
 *
 * Two modes, toggled by a push button on pin 4 (PD4):
 *
 *   CALIBRATE — Timer1 drives 500 Hz on pin 9 (OC1A).
 *               Hardware UART (pins 0/1) repurposed for target at 115200 baud.
 *               Mode LED (pin 5) on.
 *
 *   PROGRAM   — Timer1 owned by jtag2updi for host/target timeouts.
 *               500 Hz disabled. avrdude can connect normally.
 *               Mode LED (pin 5) off.
 *
 * The conflict that makes modes necessary: jtag2updi calls startTimer()
 * (TCCR1B = 0x04, CS12 normal mode) every time it waits for a host byte,
 * which destroys the CTC toggle configuration for 500 Hz.
 */

#ifndef ADDITIONS_H_
#define ADDITIONS_H_

#include <stdint.h>

namespace HexProgrammer {
enum Mode { CALIBRATE, PROGRAM };
extern Mode mode;

void init_button_led(void);
void check_mode_button(void);  // call from main loop — debounced toggle
void enter_calibrate_mode(void);
void enter_program_mode(void);
}  // namespace HexProgrammer

#endif /* ADDITIONS_H_ */
