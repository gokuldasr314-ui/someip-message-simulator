"""
SOME/IP Message Simulator
=========================
Simulates basic SOME/IP (Scalable service-Oriented MiddlewarE over IP) communication
over UDP — replicating how ECUs exchange service messages in automotive networks.

SOME/IP Header Structure (16 bytes):
  [0-1]   Service ID       : Identifies the service (e.g., 0x1234)
  [2-3]   Method ID        : Identifies the method/event (e.g., 0x0001)
  [4-7]   Length           : Length of remaining message (header from byte 8 + payload)
  [8-11]  Client ID        : Identifies the requesting client ECU
  [12-13] Session ID       : Incremented per request, used for request-response matching
  [14]    Protocol Version : Always 0x01
  [15]    Interface Version: Service interface version
  [16]    Message Type     : REQUEST, RESPONSE, NOTIFICATION, etc.
  [17]    Return Code      : E_OK, E_NOT_OK, etc.

Reference: AUTOSAR PRS_SOMEIPProtocol
"""

import socket
import struct
import threading
import time
import logging
import argparse
from enum import IntEnum

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("SOME/IP")

# ── SOME/IP Constants ─────────────────────────────────────────────────────────

class MessageType(IntEnum):
    REQUEST              = 0x00   # Client → Server: expects response
    REQUEST_NO_RETURN    = 0x01   # Client → Server: fire and forget
    NOTIFICATION         = 0x02   # Server → Client: event broadcast
    RESPONSE             = 0x80   # Server → Client: reply to REQUEST
    ERROR                = 0x81   # Server → Client: error reply

class ReturnCode(IntEnum):
    E_OK                 = 0x00   # Success
    E_NOT_OK             = 0x01   # General error
    E_UNKNOWN_SERVICE    = 0x02   # Service ID not found
    E_UNKNOWN_METHOD     = 0x03   # Method ID not found

PROTOCOL_VERSION  = 0x01
INTERFACE_VERSION = 0x01
HEADER_LENGTH     = 16    # SOME/IP header is always 16 bytes
MIN_LENGTH_FIELD  = 8     # Length field counts from byte 8 onwards

# ── SOME/IP Header Packer / Parser ────────────────────────────────────────────

def build_someip_message(service_id, method_id, client_id, session_id,
                          msg_type, return_code, payload=b""):
    """
    Pack a SOME/IP message into bytes.

    Args:
        service_id  (int): 16-bit service identifier
        method_id   (int): 16-bit method/event identifier
        client_id   (int): 16-bit client ECU identifier
        session_id  (int): 16-bit session counter
        msg_type    (MessageType): type of message
        return_code (ReturnCode): return status
        payload     (bytes): application data after the header

    Returns:
        bytes: complete SOME/IP message ready to send over UDP
    """
    length = MIN_LENGTH_FIELD + len(payload)   # counts from byte 8

    header = struct.pack(
        ">HHIHHBBBB",          # Big-endian (network byte order)
        service_id,            # 2 bytes
        method_id,             # 2 bytes
        length,                # 4 bytes
        client_id,             # 2 bytes
        session_id,            # 2 bytes
        PROTOCOL_VERSION,      # 1 byte
        INTERFACE_VERSION,     # 1 byte
        int(msg_type),         # 1 byte
        int(return_code),      # 1 byte
    )
    return header + payload


def parse_someip_message(data):
    """
    Unpack raw bytes into a SOME/IP message dictionary.

    Args:
        data (bytes): raw UDP payload received

    Returns:
        dict with keys: service_id, method_id, length, client_id,
                        session_id, protocol_version, interface_version,
                        msg_type, return_code, payload
        None if data is too short to be a valid SOME/IP message
    """
    if len(data) < HEADER_LENGTH:
        log.warning("Received packet too short (%d bytes) — not a valid SOME/IP message", len(data))
        return None

    (service_id, method_id, length,
     client_id, session_id,
     proto_ver, iface_ver,
     msg_type, return_code) = struct.unpack(">HHIHHBBBB", data[:HEADER_LENGTH])

    payload = data[HEADER_LENGTH:]

    return {
        "service_id"       : service_id,
        "method_id"        : method_id,
        "length"           : length,
        "client_id"        : client_id,
        "session_id"       : session_id,
        "protocol_version" : proto_ver,
        "interface_version": iface_ver,
        "msg_type"         : MessageType(msg_type) if msg_type in MessageType._value2member_map_ else msg_type,
        "return_code"      : ReturnCode(return_code) if return_code in ReturnCode._value2member_map_ else return_code,
        "payload"          : payload,
    }


def log_message(direction, msg, addr=None):
    """Pretty-print a parsed SOME/IP message to the console."""
    addr_str = f"  {'from' if direction == 'RX' else 'to'} {addr[0]}:{addr[1]}" if addr else ""
    payload_str = msg["payload"].decode("utf-8", errors="replace") if msg["payload"] else "(empty)"
    log.info(
        "%s %s | Service=0x%04X  Method=0x%04X  Session=%d  "
        "Type=%-20s  RC=%-12s  Payload='%s'",
        direction, addr_str,
        msg["service_id"], msg["method_id"], msg["session_id"],
        msg["msg_type"].name if isinstance(msg["msg_type"], MessageType) else msg["msg_type"],
        msg["return_code"].name if isinstance(msg["return_code"], ReturnCode) else msg["return_code"],
        payload_str,
    )


# ── SOME/IP Server (simulates an ECU exposing a service) ─────────────────────

class SomeIPServer:
    """
    Simulates an ECU acting as a SOME/IP service provider.

    Listens on a UDP port for REQUEST messages and replies with RESPONSE.
    Also periodically broadcasts NOTIFICATION (event) messages.

    Simulated service:
        Service ID : 0x1234  (e.g., a Vehicle Speed Service)
        Method  01 : get_vehicle_speed  → returns current speed as ASCII
        Method  02 : get_engine_rpm     → returns current RPM as ASCII
    """

    SERVICE_ID   = 0x1234
    METHOD_SPEED = 0x0001
    METHOD_RPM   = 0x0002
    EVENT_METHOD = 0x8001    # SOME/IP events use method IDs >= 0x8000

    def __init__(self, host="127.0.0.1", port=30490):
        self.host    = host
        self.port    = port
        self.running = False
        self.sock    = None
        self._speed  = 60     # km/h — simulated sensor value
        self._rpm    = 2500   # RPM  — simulated sensor value

    def _simulate_sensor_drift(self):
        """Slowly vary the simulated sensor values to make output realistic."""
        import random
        self._speed = max(0,   min(200, self._speed + random.randint(-5, 5)))
        self._rpm   = max(800, min(6000, self._rpm  + random.randint(-100, 100)))

    def _handle_request(self, data, client_addr):
        """Parse an incoming request and send the appropriate response."""
        msg = parse_someip_message(data)
        if msg is None:
            return

        log_message("RX ←", msg, client_addr)

        # Only handle requests for our service
        if msg["service_id"] != self.SERVICE_ID:
            self._send_error(client_addr, msg, ReturnCode.E_UNKNOWN_SERVICE)
            return

        self._simulate_sensor_drift()

        if msg["method_id"] == self.METHOD_SPEED:
            payload = f"speed={self._speed}kmh".encode()
        elif msg["method_id"] == self.METHOD_RPM:
            payload = f"rpm={self._rpm}".encode()
        else:
            self._send_error(client_addr, msg, ReturnCode.E_UNKNOWN_METHOD)
            return

        response = build_someip_message(
            service_id  = self.SERVICE_ID,
            method_id   = msg["method_id"],
            client_id   = msg["client_id"],
            session_id  = msg["session_id"],
            msg_type    = MessageType.RESPONSE,
            return_code = ReturnCode.E_OK,
            payload     = payload,
        )
        self.sock.sendto(response, client_addr)
        log_message("TX →", parse_someip_message(response), client_addr)

    def _send_error(self, client_addr, original_msg, return_code):
        """Send a SOME/IP ERROR response back to the client."""
        error = build_someip_message(
            service_id  = original_msg["service_id"],
            method_id   = original_msg["method_id"],
            client_id   = original_msg["client_id"],
            session_id  = original_msg["session_id"],
            msg_type    = MessageType.ERROR,
            return_code = return_code,
        )
        self.sock.sendto(error, client_addr)
        log.warning("TX → Error sent to %s:%d  RC=%s", client_addr[0], client_addr[1], return_code.name)

    def _notification_loop(self, broadcast_port):
        """
        Broadcast periodic NOTIFICATION messages (simulates SOME/IP events).
        In real ECUs, this is how sensor values are pushed to subscribers.
        """
        session = 1
        while self.running:
            time.sleep(3)   # broadcast every 3 seconds
            if not self.running:
                break
            self._simulate_sensor_drift()
            payload = f"speed={self._speed}kmh,rpm={self._rpm}".encode()
            notification = build_someip_message(
                service_id  = self.SERVICE_ID,
                method_id   = self.EVENT_METHOD,
                client_id   = 0x0000,
                session_id  = session,
                msg_type    = MessageType.NOTIFICATION,
                return_code = ReturnCode.E_OK,
                payload     = payload,
            )
            broadcast_addr = ("127.0.0.1", broadcast_port)
            self.sock.sendto(notification, broadcast_addr)
            log.info("TX → NOTIFICATION broadcast | Payload='%s'", payload.decode())
            session = (session + 1) & 0xFFFF   # wrap at 16-bit max

    def start(self, notification_port=30491):
        """Start the SOME/IP server — listens for requests and sends notifications."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.settimeout(1.0)
        self.running = True

        log.info("=" * 60)
        log.info("SOME/IP Server started on %s:%d", self.host, self.port)
        log.info("Service ID : 0x%04X  (Vehicle Data Service)", self.SERVICE_ID)
        log.info("Methods    : 0x0001=get_speed  0x0002=get_rpm")
        log.info("=" * 60)

        # Start notification broadcast in background thread
        notif_thread = threading.Thread(
            target=self._notification_loop,
            args=(notification_port,),
            daemon=True
        )
        notif_thread.start()

        # Main receive loop
        try:
            while self.running:
                try:
                    data, addr = self.sock.recvfrom(4096)
                    self._handle_request(data, addr)
                except socket.timeout:
                    continue   # check self.running again
        except KeyboardInterrupt:
            log.info("Server shutting down...")
        finally:
            self.running = False
            self.sock.close()


# ── SOME/IP Client (simulates an ECU consuming a service) ────────────────────

class SomeIPClient:
    """
    Simulates an ECU acting as a SOME/IP service consumer.

    Sends REQUEST messages to the server and processes RESPONSE / NOTIFICATION.
    """

    CLIENT_ID  = 0xAB01   # Unique ID for this client ECU
    SERVICE_ID = 0x1234

    def __init__(self, server_host="127.0.0.1", server_port=30490,
                 listen_port=30491):
        self.server_addr  = (server_host, server_port)
        self.listen_port  = listen_port
        self.session_id   = 1
        self.sock         = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", listen_port))
        self.sock.settimeout(2.0)

    def _next_session(self):
        sid = self.session_id
        self.session_id = (self.session_id + 1) & 0xFFFF
        return sid

    def request(self, method_id):
        """Send a SOME/IP REQUEST and wait for RESPONSE."""
        session = self._next_session()
        req = build_someip_message(
            service_id  = self.SERVICE_ID,
            method_id   = method_id,
            client_id   = self.CLIENT_ID,
            session_id  = session,
            msg_type    = MessageType.REQUEST,
            return_code = ReturnCode.E_OK,
        )
        self.sock.sendto(req, self.server_addr)
        log_message("TX →", parse_someip_message(req), self.server_addr)

        try:
            data, addr = self.sock.recvfrom(4096)
            msg = parse_someip_message(data)
            if msg:
                log_message("RX ←", msg, addr)
                return msg
        except socket.timeout:
            log.error("Timeout — no response from server for method 0x%04X", method_id)
            return None

    def listen_for_notifications(self, duration=10):
        """Listen for SOME/IP NOTIFICATION broadcasts for a given number of seconds."""
        log.info("Listening for NOTIFICATION events for %ds...", duration)
        deadline = time.time() + duration
        while time.time() < deadline:
            try:
                data, addr = self.sock.recvfrom(4096)
                msg = parse_someip_message(data)
                if msg and msg["msg_type"] == MessageType.NOTIFICATION:
                    log_message("RX ← NOTIF", msg, addr)
            except socket.timeout:
                continue

    def close(self):
        self.sock.close()


# ── Demo: run server + client in the same process ────────────────────────────

def run_demo():
    """
    Runs a full demo:
      1. Starts the SOME/IP server in a background thread
      2. Starts the client, sends requests, and listens for notifications
    """
    log.info("Starting SOME/IP Simulator Demo")
    log.info("This simulates two ECUs communicating over UDP using SOME/IP protocol\n")

    # Start server in background
    server = SomeIPServer(host="127.0.0.1", port=30490)
    server_thread = threading.Thread(target=server.start, kwargs={"notification_port": 30491}, daemon=True)
    server_thread.start()
    time.sleep(0.3)   # give server time to bind

    # Start client
    client = SomeIPClient(server_host="127.0.0.1", server_port=30490, listen_port=30491)

    try:
        log.info("\n── Sending REQUEST: get_vehicle_speed (Method 0x0001) ──")
        client.request(method_id=0x0001)
        time.sleep(0.5)

        log.info("\n── Sending REQUEST: get_engine_rpm (Method 0x0002) ──")
        client.request(method_id=0x0002)
        time.sleep(0.5)

        log.info("\n── Sending REQUEST: unknown method (expect E_UNKNOWN_METHOD) ──")
        client.request(method_id=0x00FF)
        time.sleep(0.5)

        log.info("\n── Waiting for NOTIFICATION events (next 10 seconds) ──")
        client.listen_for_notifications(duration=10)

    except KeyboardInterrupt:
        log.info("Demo interrupted.")
    finally:
        client.close()
        server.running = False
        log.info("Demo complete.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SOME/IP Message Simulator")
    parser.add_argument(
        "--mode",
        choices=["demo", "server", "client"],
        default="demo",
        help="demo: run both server+client | server: server only | client: client only"
    )
    parser.add_argument("--host",         default="127.0.0.1")
    parser.add_argument("--server-port",  type=int, default=30490)
    parser.add_argument("--client-port",  type=int, default=30491)
    args = parser.parse_args()

    if args.mode == "demo":
        run_demo()

    elif args.mode == "server":
        srv = SomeIPServer(host=args.host, port=args.server_port)
        srv.start(notification_port=args.client_port)

    elif args.mode == "client":
        cli = SomeIPClient(
            server_host=args.host,
            server_port=args.server_port,
            listen_port=args.client_port,
        )
        log.info("Sending speed request...")
        cli.request(0x0001)
        log.info("Sending RPM request...")
        cli.request(0x0002)
        log.info("Listening for notifications (10s)...")
        cli.listen_for_notifications(10)
        cli.close()
