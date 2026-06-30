# SOME/IP Message Simulator

A Python simulation of the **SOME/IP (Scalable service-Oriented MiddlewarE over IP)** protocol — the automotive middleware standard used for ECU-to-ECU communication over Ethernet in modern vehicles (AUTOSAR).

This project replicates how real ECUs exchange service messages over UDP, covering REQUEST, RESPONSE, NOTIFICATION, and ERROR message types.

---

## What Is SOME/IP?

SOME/IP is the communication protocol used in automotive Ethernet networks (e.g., BMW, Mercedes, Volkswagen Group vehicles). ECUs expose **services** (like vehicle speed, engine RPM, ADAS sensor data) that other ECUs can **request** or **subscribe to as events**.

This simulator implements the core SOME/IP message structure defined in the AUTOSAR specification.

---

## SOME/IP Header Structure (16 bytes)

```
 Byte 0-1  : Service ID        (e.g., 0x1234 = Vehicle Data Service)
 Byte 2-3  : Method ID         (e.g., 0x0001 = get_speed)
 Byte 4-7  : Length            (remaining bytes after byte 8)
 Byte 8-9  : Client ID         (requesting ECU identifier)
 Byte 10-11: Session ID        (incremented per request)
 Byte 12   : Protocol Version  (always 0x01)
 Byte 13   : Interface Version
 Byte 14   : Message Type      (REQUEST / RESPONSE / NOTIFICATION / ERROR)
 Byte 15   : Return Code       (E_OK / E_NOT_OK / E_UNKNOWN_SERVICE etc.)
 Byte 16+  : Payload           (application data)
```

---

## Features

- Full SOME/IP header packing and parsing using Python `struct`
- **Server** — simulates an ECU exposing a Vehicle Data Service (speed, RPM)
- **Client** — simulates an ECU sending requests and receiving responses
- **Notification events** — server broadcasts periodic sensor updates (like SOME/IP event groups)
- **Error handling** — returns `E_UNKNOWN_SERVICE` and `E_UNKNOWN_METHOD` correctly
- **Session tracking** — session ID increments per request for request-response matching
- All communication over **UDP** (same transport as real Automotive Ethernet SOME/IP)

---

## Project Structure

```
someip_simulator/
│
├── someip_simulator.py     # Main simulator — server, client, message builder/parser
└── README.md
```

---

## How to Run

**No external dependencies — standard Python 3 only.**

### Run the full demo (server + client in one terminal)
```bash
python someip_simulator.py --mode demo
```

### Run server and client separately (two terminals)

Terminal 1 — Server:
```bash
python someip_simulator.py --mode server
```

Terminal 2 — Client:
```bash
python someip_simulator.py --mode client
```

---

## Example Output

```
10:22:01  INFO      SOME/IP Server started on 127.0.0.1:30490
10:22:01  INFO      Service ID : 0x1234  (Vehicle Data Service)
10:22:01  INFO      Methods    : 0x0001=get_speed  0x0002=get_rpm

10:22:01  INFO      TX → to 127.0.0.1:30490 | Service=0x1234  Method=0x0001  Session=1  Type=REQUEST               RC=E_OK          Payload=''
10:22:01  INFO      RX ← from 127.0.0.1:30491 | Service=0x1234  Method=0x0001  Session=1  Type=RESPONSE              RC=E_OK          Payload='speed=63kmh'

10:22:01  INFO      TX → to 127.0.0.1:30490 | Service=0x1234  Method=0x0002  Session=2  Type=REQUEST               RC=E_OK          Payload=''
10:22:01  INFO      RX ← from 127.0.0.1:30491 | Service=0x1234  Method=0x0002  Session=2  Type=RESPONSE              RC=E_OK          Payload='rpm=2480'

10:22:04  INFO      TX → NOTIFICATION broadcast | Payload='speed=58kmh,rpm=2390'
10:22:07  INFO      TX → NOTIFICATION broadcast | Payload='speed=61kmh,rpm=2450'
```

---

## Relevance to Real Automotive Development

| This Simulator | Real ECU Equivalent |
|---|---|
| UDP socket server | SOME/IP service provider ECU |
| UDP socket client | SOME/IP service consumer ECU |
| Periodic NOTIFICATION | SOME/IP event group subscription |
| Service ID / Method ID | AUTOSAR service interface definition |
| Session ID tracking | Request-response correlation in Gateway ECUs |
| E_UNKNOWN_METHOD error | Diagnostic response for unsupported services |

In production, SOME/IP runs over Automotive Ethernet (100BASE-T1 or 1000BASE-T1) and is configured via AUTOSAR ARXML service definitions. This simulator covers the protocol layer that sits above the transport.

---

## Background

Built as a personal project to deepen understanding of Automotive Ethernet communication protocols outside of the HIL lab — complementing hands-on validation work with BMW ECUs using CANoe and Wireshark.

---

## References

- AUTOSAR PRS_SOMEIPProtocol Specification
- SOME/IP Protocol Specification v1.5
- ISO 17215 (Vehicle Ethernet)
