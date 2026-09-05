"""Opt-in native executable check, exclusively against a loopback test socket."""
import os
import shlex
import socket
import struct
import subprocess
import threading
import unittest

from test_modpoll import export_modpoll, inputs, point, text


class NativeProconxTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("MODBUS_TEST_PROCONX_BIN"), "native proconX executable not configured")
    def test_generated_commands_read_exact_pdu_and_decode_values(self):
        cases = [
            ("uint16", "holding-register", [60000], "60000"),
            ("int16", "input-register", [65534], "-2"),
            ("float32", "holding-register", list(struct.unpack(">HH", struct.pack(">f", 12.5))), "12.5"),
            ("float64", "input-register", list(struct.unpack(">HHHH", struct.pack(">d", 12.5))), "12.5"),
        ]
        for datatype, area, words, expected in cases:
            with self.subTest(datatype=datatype), socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                listener.settimeout(5)
                observed = []
                errors = []

                def receive_exact(connection, count):
                    value = b""
                    while len(value) < count:
                        chunk = connection.recv(count - len(value))
                        if not chunk:
                            raise AssertionError("native client closed an incomplete request")
                        value += chunk
                    return value

                def serve():
                    try:
                        with listener.accept()[0] as connection:
                            connection.settimeout(5)
                            header = receive_exact(connection, 7)
                            transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                            pdu = receive_exact(connection, length - 1)
                            function, offset, count = struct.unpack(">BHH", pdu)
                            observed.append((protocol, unit, function, offset, count))
                            if function not in (3, 4) or offset != 0 or count != len(words):
                                raise AssertionError("unexpected physical read")
                            body = bytes([function, len(words) * 2]) + struct.pack(">" + "H" * len(words), *words)
                            connection.sendall(struct.pack(">HHHB", transaction, 0, len(body) + 1, unit) + body)
                    except Exception as exc:
                        errors.append(str(exc))

                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                canonical, plan = inputs(point(protocol_offset=0, datatype=datatype, area=area,
                    word_span=len(words), scale=None, engineering_offset=0))
                result = export_modpoll(canonical, plan, profile="proconx-cli")
                self.assertEqual("generated", result.status)
                command = next(line for line in text(result, "commands.txt").splitlines() if line.startswith("modpoll "))
                command = command.replace("${MODBUS_HOST}", "127.0.0.1").replace("${MODBUS_PORT}", str(listener.getsockname()[1]))
                arguments = shlex.split(command)
                arguments[0] = os.environ["MODBUS_TEST_PROCONX_BIN"]
                completed = subprocess.run(arguments, capture_output=True, text=True, timeout=8)
                worker.join(timeout=6)
                self.assertFalse(worker.is_alive())
                self.assertEqual([], errors)
                self.assertEqual([(0, 1, 3 if area == "holding-register" else 4, 0, len(words))], observed)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn(expected, completed.stdout)
