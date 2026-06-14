# AI-Controlled Autonomous Rover (A.C.A.R.)

An end-to-end autonomous rover system that connects an LLM-powered control layer to a Python host app, a master ESP32, and a slave ESP32 over ESP-NOW.

## Overview

A.C.A.R. is built around a layered control pipeline:

1. **User / LLM layer** generates a high-level navigation or action plan.
2. **Python host** converts that plan into structured packets.
3. **Master ESP32** receives packets over serial and forwards them over ESP-NOW.
4. **Slave ESP32** validates commands, executes motion, and sends ACK/telemetry back.
5. **Telemetry loop** keeps the system aware of safety, battery, obstacle distance, and sequence progress.

This repository is designed so the rover can be controlled safely, step-by-step, with packet acknowledgements and periodic telemetry.

---

## Features

* LLM-assisted command generation
* Packet-based motion control
* ACK / reject / safe-state responses
* Heartbeat and resume support
* Periodic telemetry from rover to controller
* Sequence-based execution with step indexing
* Safety-oriented communication flow

---

## System Architecture

### Main components

* **Python host application**

  * Receives LLM output
  * Converts commands into packets
  * Sends packets to the master ESP32 through serial
  * Reads ACK / telemetry packets coming back

* **Master ESP32**

  * Bridges serial communication and ESP-NOW
  * Forwards motion packets to the slave
  * Returns slave responses to Python

* **Slave ESP32**

  * Receives packets over ESP-NOW
  * Validates packet content and CRC
  * Executes motion commands
  * Sends ACK, safety, and telemetry packets back

* **LLM layer**

  * Produces natural-language or structured navigation instructions
  * Can be used for planning, decision-making, or command generation

---

## Packet Structure

### 1) MotionPacket_t — 14 bytes

Used for motion, heartbeat, ACK, abort, and resume workflows.

| Byte(s) | Field       | Description                             |
| ------- | ----------- | --------------------------------------- |
| 0       | magic[0]    | 0xAB                                    |
| 1       | magic[1]    | 0xCD                                    |
| 2       | version     | 0x02                                    |
| 3       | packet_type | Packet type identifier                  |
| 4       | sequence_id | Packet number for ACK matching          |
| 5       | total_steps | Number of steps in the full sequence    |
| 6       | step_index  | Current step index (0-based)            |
| 7       | command     | Motion/action command                   |
| 8–9     | duration_ms | Duration in milliseconds, little-endian |
| 10      | speed_pct   | Speed from 0–100                        |
| 11      | flags       | Extra options                           |
| 12–13   | crc16       | CRC16 checksum, little-endian           |

### 2) TelemetryPacket_t — 14 bytes

Sent from slave to master every 500 ms.

| Byte(s) | Field             | Description                            |
| ------- | ----------------- | -------------------------------------- |
| 0       | magic[0]          | 0xAB                                   |
| 1       | magic[1]          | 0xCD                                   |
| 2       | version           | 0x02                                   |
| 3       | packet_type       | 0x20                                   |
| 4       | safety_state      | Current safety state                   |
| 5       | battery_pct       | Battery percentage                     |
| 6–7     | obstacle_front_cm | Front obstacle distance, little-endian |
| 8–9     | obstacle_rear_cm  | Rear obstacle distance, little-endian  |
| 10      | current_step      | Current executing step                 |
| 11      | total_steps       | Total steps in sequence                |
| 12–13   | crc16             | CRC16 checksum, little-endian          |

> Important: `TELEMETRY_PKT_SIZE` should be **14**, not 15.

---

## Packet Types

| Value | Name             | Direction      | Meaning                 |
| ----- | ---------------- | -------------- | ----------------------- |
| 0x01  | PKT_MOTION_STEP  | Master → Slave | Execute one motion step |
| 0x02  | PKT_SEQ_END      | Master → Slave | Sequence finished       |
| 0x03  | PKT_ABORT        | Master → Slave | Emergency stop          |
| 0x04  | PKT_HEARTBEAT    | Master → Slave | Keep-alive ping         |
| 0x05  | PKT_RESUME       | Master → Slave | Resume from safe state  |
| 0x10  | PKT_ACK_OK       | Slave → Master | Packet accepted         |
| 0x11  | PKT_ACK_REJECTED | Slave → Master | Packet rejected         |
| 0x12  | PKT_SAFE_STATE   | Slave → Master | Safety event occurred   |
| 0x20  | PKT_TELEMETRY    | Slave → Master | Status update           |

---

## Workflow

### End-to-end flow

1. **User gives a command**

   * Example: “Move forward 2 meters, then turn left.”

2. **LLM interprets the request**

   * The LLM can convert user intent into a structured motion plan.
   * Output should be broken into steps that the rover can execute safely.

3. **Python host builds packets**

   * Each step becomes a `MotionPacket_t`.
   * Packet fields are filled with sequence ID, step index, command, duration, speed, and CRC.

4. **Python sends packet to master ESP32 over serial**

   * The master acts as the bridge between PC and rover network.

5. **Master forwards packet over ESP-NOW**

   * The packet is relayed to the slave ESP32.

6. **Slave validates the packet**

   * Checks magic bytes, version, packet type, and CRC.
   * If valid, it responds with `PKT_ACK_OK`.
   * If invalid, it responds with `PKT_ACK_REJECTED`.

7. **Slave executes motion**

   * Motor control runs according to the command and duration.

8. **Slave sends telemetry every 500 ms**

   * Safety state
   * Battery level
   * Front and rear obstacle distances
   * Current step and total steps

9. **Master relays telemetry and ACK back to Python**

   * Python updates control state and decides the next step.

10. **Sequence completes**

* Master sends `PKT_SEQ_END` when all steps are finished.

---

## Setup

### Prerequisites

* Python 3.10+ on the host machine
* ESP32 toolchain / Arduino IDE / PlatformIO for ESP32 firmware
* Serial connection between PC and master ESP32
* ESP-NOW communication configured between master and slave ESP32
* Compatible motor driver / rover hardware

### Python environment

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
# .venv\Scripts\activate    # Windows
```

Install dependencies:

```bash
pip install -r requirements.txt
```

If the project uses separate packages for the LLM or serial stack, install them as well.

---

## Configuration

Before running the system, verify these values:

* Serial port for the master ESP32
* Baud rate used by Python and firmware
* ESP-NOW peer MAC addresses
* Packet magic bytes and version
* CRC16 algorithm on both sender and receiver
* Telemetry packet size: **14 bytes**
* Motor command mapping
* Safety thresholds for obstacle distance and battery percentage

---

## Running the System

### 1. Flash the ESP32 firmware

Flash both boards with the corresponding firmware:

* **Master ESP32 firmware**
* **Slave ESP32 firmware**

Ensure that ESP-NOW peer pairing is configured correctly.

### 2. Start the Python host

Run the Python controller:

```bash
python main.py
```

(or the repository’s entry file, if named differently)

### 3. Send a command

Provide either:

* a natural language prompt to the LLM, or
* a structured motion request

The host will convert it into packets and begin the motion sequence.

### 4. Monitor telemetry and ACKs

The Python app should continuously receive:

* ACK status
* safety events
* telemetry updates
* sequence completion notifications

---

## LLM Integration

The LLM acts as the planning layer, not the low-level motor layer.

### Suggested LLM responsibility

* Convert user intent into a safe step sequence
* Split large instructions into smaller motion actions
* Normalize outputs into a consistent schema
* Avoid generating ambiguous commands

### Suggested structured output

```json
{
  "sequence": [
    { "command": "forward", "duration_ms": 2000, "speed_pct": 60 },
    { "command": "left", "duration_ms": 900, "speed_pct": 50 }
  ]
}
```

The Python layer should validate this output before sending packets.

### Recommended flow for LLM setup

1. Accept user instruction.
2. Ask the LLM to return a structured motion plan.
3. Validate the plan against allowed commands.
4. Convert each step into a packet.
5. Execute step-by-step with ACK checks.
6. Stop immediately if a safety packet or reject is received.

---

## Safety and Recovery

The rover should stop or pause when any of the following occurs:

* CRC mismatch
* Invalid magic bytes
* Unsupported packet type
* Safety state trigger
* Obstacle threshold violation
* Low battery warning
* Lost heartbeat

Recovery can be handled using:

* `PKT_ABORT` for immediate stop
* `PKT_RESUME` for restarting from a safe state
* `PKT_HEARTBEAT` for keep-alive monitoring

---

## Troubleshooting

### Telemetry packets are misread

Check that `TELEMETRY_PKT_SIZE = 14`.

### ACKs are not arriving

Verify:

* ESP-NOW peer MAC address
* same packet version on both ends
* CRC16 implementation
* serial parsing boundaries

### Commands execute incorrectly

Confirm that the command enum mapping is identical in:

* Python host
* master ESP32
* slave ESP32

### Sequence desync

Check:

* `sequence_id`
* `step_index`
* `total_steps`
* whether a packet was dropped or rejected

---

## Suggested Repository Structure

```bash
.
├── esp_interface.py
├── main.py
├── packet.py / packet.h
├── llm/
├── firmware/
│   ├── master/
│   └── slave/
├── requirements.txt
└── README.md
```

---

## Notes for Contributors

* Keep packet layouts synchronized across Python and firmware.
* Any packet field change must be reflected in CRC calculation.
* Do not change telemetry packet size unless both sender and receiver are updated.
* Validate LLM output before generating motion packets.

---



---

## Acknowledgements

Built for autonomous rover control using ESP32, Python, serial communication, ESP-NOW, and LLM-based planning.
