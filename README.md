# AI-Controlled Autonomous Rover (A.C.A.R.)

An AI-powered autonomous rover platform that combines local speech recognition, natural language understanding, ESP32-based wireless control, and real-time telemetry into a complete robotics system.

The project enables a user to issue natural language voice commands such as:

* "Move forward for 3 seconds"
* "Turn left slowly"
* "What is your battery level?"
* "Stop immediately"

The command is processed by the AI layer, converted into motion packets, transmitted wirelessly through ESP-NOW, and executed by the rover in real time.

---

# System Architecture

```text
User Voice
    │
    ▼
faster-whisper (Speech-to-Text)
    │
    ▼
Intent Classifier
    │
    ├── HARDWARE_MOTION
    │       │
    │       ▼
    │   Motion Parser
    │       │
    │       ▼
    │  Packet Generator
    │
    ├── HARDWARE_QUERY
    │
    └── CONVERSATION
            │
            ▼
        Ollama + Qwen
            │
            ▼
      Natural Language Response

                    Serial (USB)
Python Host ─────────────────────────► Master ESP32
                                          │
                                          ▼
                                   ESP-NOW Network
                                          │
                                          ▼
                                     Slave ESP32
                                          │
                                          ▼
                                  Motor Driver + Sensors
```

---

# Repository Structure

```text
AI-Controlled-Autonomous-Rover-A.C.A.R.-/
│
├── .gitignore
├── README.md
├── ROVER.code-workspace
├── ROVER.txt
│
├── rover_ai/
│   ├── main.py
│   ├── esp_interface.py
│   ├── intent_classifier.py
│   ├── motion_parser.py
│   └── orchestrator.py
│
├── rover_slave/
│   ├── .gitignore
│   ├── platformio.ini
│   ├── include/
│   │   ├── README
│   │   ├── config.h
│   │   ├── crc16.h
│   │   ├── motors.h
│   │   └── packet.h
│   ├── lib/
│   │   └── README
│   └── src/
│       └── main.cpp
│
└── rvr_master/
    ├── .gitignore
    ├── platformio.ini
    ├── include/
    │   ├── README
    │   ├── config.h
    │   ├── crc16.h
    │   └── packet.h
    ├── lib/
    │   └── README
    └── src/
        └── main.cpp
```

---

# Hardware Requirements

| Component                 | Quantity |
| ------------------------- | -------- |
| ESP32 DevKit V1           | 2        |
| L298N Motor Driver        | 1        |
| DC Gear Motors            | 2–4      |
| HC-SR04 Ultrasonic Sensor | 2        |
| Battery Pack              | 1        |
| USB Cable                 | 2        |
| Chassis & Wheels          | 1        |

---

# Software Requirements

## Python

* Python 3.10+
* pip
* virtualenv

## ESP32 Development

* PlatformIO
* Arduino Framework
* ESP32 Board Support Package

## AI Stack

* Ollama
* Qwen 2
* Faster Whisper

---

# Installation

## 1. Clone Repository

```bash
git clone https://github.com/timon221b/AI-Controlled-Autonomous-Rover-A.C.A.R.-.git

cd AI-Controlled-Autonomous-Rover-A.C.A.R.-
```

---

## 2. Create Python Environment

### Linux / macOS

```bash
python -m venv .venv
source .venv/bin/activate
```

### Windows

```powershell
python -m venv .venv

.venv\Scripts\activate
```

---

## 3. Install Python Dependencies

```bash
pip install --upgrade pip

pip install -r requirements.txt
```

If requirements.txt is unavailable:

```bash
pip install pyserial
pip install numpy
pip install requests
pip install faster-whisper
pip install sounddevice
pip install python-dotenv
```

---

# Ollama Setup

Install Ollama from:

https://ollama.com

Verify installation:

```bash
ollama --version
```

---

## Download Qwen Model

Lightweight version:

```bash
ollama pull qwen2:1.5b
```

Larger version:

```bash
ollama pull qwen2:7b
```

Test model:

```bash
ollama run qwen2:1.5b "Hello Rover"
```

---

## Start Ollama Service

```bash
ollama serve
```

Default endpoint:

```text
http://localhost:11434
```

---

# ESP32 Firmware Setup

Both ESP32 projects use PlatformIO.

Install PlatformIO:

```bash
pip install platformio
```

---

## Flash Slave ESP32

```bash
cd rover_slave

platformio run -e esp32doit-devkit-v1

platformio run -t upload -e esp32doit-devkit-v1

platformio run -t monitor -e esp32doit-devkit-v1
```

Expected:

```text
[SLAVE] ESP-NOW initialized
[SLAVE] Waiting for packets...
```

---

## Flash Master ESP32

```bash
cd rvr_master

platformio run -e esp32doit-devkit-v1

platformio run -t upload -e esp32doit-devkit-v1

platformio run -t monitor -e esp32doit-devkit-v1
```

Expected:

```text
[MASTER] Serial bridge ready
[MASTER] ESP-NOW initialized
```

---

# ESP-NOW Configuration

Update peer MAC addresses in:

```text
rvr_master/include/config.h
rover_slave/include/config.h
```

Example:

```cpp
uint8_t slave_mac[6] = {
    0xAA,
    0xBB,
    0xCC,
    0xDD,
    0xEE,
    0xFF
};
```

Replace with actual hardware MAC address.

---

# Serial Configuration

Default baud rate:

```text
115200
```

Example Python configuration:

```python
SERIAL_PORT = "COM3"
BAUD_RATE = 115200
```

Linux:

```python
SERIAL_PORT = "/dev/ttyUSB0"
```

---

# Running the System

Start Ollama:

```bash
ollama serve
```

Activate environment:

```bash
source .venv/bin/activate
```

Run rover controller:

```bash
cd rover_ai

python main.py
```

Expected output:

```text
Loading Whisper Model...
Connecting to ESP32...
Starting Telemetry Thread...
Rover Ready
Press Enter To Speak
```

---

# Communication Protocol

## Motion Packet

Size:

```text
14 Bytes
```

Fields:

```text
Magic
Version
Packet Type
Sequence ID
Total Steps
Step Index
Command
Duration
Speed
Flags
CRC16
```

---

## Telemetry Packet

Size:

```text
14 Bytes
```

Fields:

```text
Safety State
Battery Percentage
Obstacle Distance Front
Obstacle Distance Rear
Current Step
Total Steps
CRC16
```

---

# Important Fix

In:

```text
rover_ai/esp_interface.py
```

Use:

```python
TELEMETRY_PKT_SIZE = 14
```

NOT:

```python
TELEMETRY_PKT_SIZE = 15
```

Using 15 causes telemetry desynchronization and packet corruption.

---

# End-to-End Workflow

```text
Voice Command
      │
      ▼
Speech Recognition
      │
      ▼
Intent Classification
      │
      ▼
Motion Parsing
      │
      ▼
Packet Generation
      │
      ▼
Master ESP32
      │
      ▼
ESP-NOW
      │
      ▼
Slave ESP32
      │
      ▼
Motor Execution
      │
      ▼
Telemetry Feedback
      │
      ▼
Python Host
```

---

# Troubleshooting

### ESP32 Not Detected

```bash
platformio device list
```

---

### Serial Permission Error

Linux:

```bash
sudo usermod -a -G dialout $USER
```

Logout and login again.

---

### Force Clean Build

```bash
platformio run -t clean

platformio run
```

---

### Ollama Connection Refused

```bash
ollama serve
```

must be running before starting the rover.

---

### No Telemetry Received

Check:

```python
TELEMETRY_PKT_SIZE = 14
```

and verify Master ↔ Slave ESP-NOW pairing.

---

# Future Improvements

* Camera integration
* Vision-based navigation
* Obstacle avoidance with AI planning
* Multi-rover coordination
* GPS waypoint navigation
* Remote dashboard
* Autonomous mission execution

---


# Author

Ansh Kashyap, Darshan Malviya, Krishna Kodape

Electronics & Telecommunication Engineering

AI-Controlled Autonomous Rover (A.C.A.R.)
