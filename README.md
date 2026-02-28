# About
Cynthia is a MIDI Synthesizer supporting most General MIDI devices built around [RP2040 (Raspberry Pi Pico)](https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html) 
and [Adafruit VS1053B](https://www.adafruit.com/product/1381).

It features MIDI input (Type A 3.5mm TR(R)S) to connect controller and USB input. It sends both USB and MIDI input commands
out to VS1053B board which processes them into audible sound. It has no built-in amplifier, so external amp may be needed.

More can be read on [Polish forum Forbot](https://forbot.pl/forum/topic/25873-cynthia-syntezator-midi-trs-a-oraz-usb/).

**All code is AI generated and is mostly functional (there are some bugs, but for my use cases I don't need to fix them)**

# Hardware
* Raspberry Pi Pico (RP2040)
* Adafruit VS1053 Codec
* Two cheap TRRS connectors (red boards)
* A bit of wire
* Two resistors (330R-10K)
* Some 3D printing filament (and a 3D printer of course)
* MicroUSB to USB-A (or C if preferred) cable
* A bit of insulation tape
* 5pcs of M3x16 screws for chassis assembly

# Connections
| **Board A**       | **Connection A** | **Board B**       | **Connection B** |
|-------------------|------------------|-------------------|------------------|
| VS1053B Codec     | IO 0             | VS1053B Codec     | GND              |
| VS1053B Codec     | IO 1             | VS1053B Codec     | 3V3              |
| Pi Pico           | VBUS             | VS1053B Codec     | VCC              |
| Pi Pico           | GND              | VS1053B Codec     | GND              |
| Pi Pico           | GP12             | VS1053B Codec     | RST              |
| Pi Pico           | GP0 (UART 0 TX)  | VS1053B Codec     | RX               |
| Pi Pico           | GP1 (UART 0 RX)  | Custom MIDI Board | RX               |
| Pi Pico           | VBUS             | Custom MIDI Board | 5V               |
| Pi Pico           | 3V3              | Custom MIDI Board | 3V3              |
| Pi Pico           | GND              | Custom MIDI Board | GND              |
| VS1053B Codec     | L                | TRRS 1            | Tip              |
| VS1053B Codec     | R                | TRRS 1            | Ring 1           |
| VS1053B Codec     | AGND             | TRRS 1            | Sleeve           |
| Custom MIDI Board | SINK             | TRRS 2            | Tip              |
| Custom MIDI Board | SRC              | TRRS 2            | Ring 1           |

Of course, you also need to plug-in USB cable into Pico port :)

# Custom MIDI Board
Custom MIDI board used for RX is also available in this repository. It's a simple PC817 coupled with 2N2222 in darlington 
configuration to ensure proper rise/fall times. All necessary resistors are implemented. Board uses THT components and 
0.5mm copper to copper spacing to make it easy to manufacture at home.

# Software
This project comes with two example Python software units that can be used to control this device. 

## Cynthia Controller
This software is used to change instruments (for Cynthia remember to keep channel at 1) for any COM MIDI device connected 
to your PC.

## Cynthia MIDI Player
This software can be used to load and play MIDI music using Cynthia as USB to Audio output interface. Of course, it uses
CDC port which is not standard, but modification to make it work as USB-MIDI device is possible (for author CDC is preferred).
