"""Device drivers. Model-specific behavior stays in drivers and descriptions."""
from __future__ import annotations
import copy
import importlib
import math
import socket
import time
from concurrent.futures import CancelledError
from . import arrays, text_protocol
from .store import now


def parameters(spec, supplied):
    fields = spec.get("parameters", {})
    unknown = set(supplied) - set(fields)
    if unknown:
        raise ValueError("Unknown parameters: " + ", ".join(sorted(unknown)))
    result = {}
    for name, field in fields.items():
        if name not in supplied:
            if "default" in field:
                value = field["default"]
            elif field.get("required", True):
                raise ValueError("Missing parameter: " + name)
            else:
                continue
        else:
            value = supplied[name]
        kind = field.get("type")
        if kind in {"number", "integer"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(name + " must be a finite number")
            if kind == "integer" and not isinstance(value, int):
                raise ValueError(name + " must be an integer")
            if "minimum" in field and value < field["minimum"] or "maximum" in field and value > field["maximum"]:
                raise ValueError(name + " is outside the operation's range")
        elif kind == "string" and not isinstance(value, str):
            raise ValueError(name + " must be a string")
        elif kind == "boolean" and not isinstance(value, bool):
            raise ValueError(name + " must be boolean")
        elif kind == "array":
            if not isinstance(value,list):
                raise ValueError(name + " must be an array")
            if len(value) < field.get("minItems",0) or len(value) > field.get("maxItems",1000000):
                raise ValueError(name + " has an unsupported length")
            if "items" in field:
                for item in value:
                    parameters({"parameters":{"item":field["items"]}},{"item":item})
        elif kind == "object" and not isinstance(value,dict):
            raise ValueError(name + " must be an object")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(name + " must be one of " + str(field["enum"]))
        result[name] = value
    return result


class UnconfirmedChange(RuntimeError):
    """A change command may have reached the instrument, but its completion was not confirmed."""


class Refused(RuntimeError):
    """The instrument answered a command with an error reply: it did not act on it."""


class Driver:
    def __init__(self, device_id, config, registry):
        self.id, self.config, self.registry = device_id, config, registry

    def describe(self):
        raise NotImplementedError

    def invoke(self, operation, arguments, cancelled):
        raise NotImplementedError

    def close(self):
        pass


class Released(Driver):
    """Keep the device description without opening its SDK or connection on restart."""
    def __init__(self, description):
        self.description = description

    def describe(self):
        return self.description


class Simulated(Driver):
    def __init__(self, *args):
        super().__init__(*args)
        self.position = 0.0
        self.exposure = 0.02

    def describe(self):
        duration = {"type":"number", "unit":"s", "minimum":0, "maximum":60, "default":0.05}
        operations = {
            "move_to": {"description":"Move and report the settled simulated angle", "parameters":{
                "angle_deg":{"type":"number", "unit":"deg"}, "duration":duration}},
            "position": {"description":"Read the simulated angle", "effect":"observe", "parameters":{}},
            "configure": {"description":"Set simulated acquisition duration", "parameters":{
                "exposure_s":{**duration, "default":0.02}}},
            "measure": {"description":"Sample ideal half-wave-plate transmission", "parameters":{}},
            "wait": {"description":"Wait without changing the device", "parameters":{"duration":duration}},
        }
        kind = self.config.get("kind", "stage")
        selected = ["configure", "measure", "wait"] if kind == "meter" else ["move_to", "position", "wait"]
        return {"name":self.config.get("name", self.id), "mode":"simulation", "kind":kind,
                "model":"ideal-polarization/1", "operations":{k:operations[k] for k in selected}}

    def invoke(self, operation, arguments, cancelled):
        duration = arguments.get("duration", self.exposure if operation == "measure" else 0)
        if cancelled.wait(duration):
            raise CancelledError("Operation cancelled")
        if operation == "move_to":
            self.position = arguments["angle_deg"]
        elif operation == "configure":
            self.exposure = arguments["exposure_s"]
            return {"exposure_s":self.exposure, "time":now(), "source":"simulation"}
        elif operation == "wait":
            return {"elapsed_s":duration, "time":now(), "source":"simulation"}
        elif operation == "measure":
            stage_id = self.config.get("stage")
            stage = self.registry.get(stage_id)
            angle = stage.position if isinstance(stage, Simulated) else 0
            return {"power":math.cos(math.radians(2 * angle)) ** 2, "unit":"W",
                    "angle_actual":angle, "time":now(), "source":"simulation"}
        return {"angle_actual":self.position, "unit":"deg", "time":now(), "source":"simulation"}


class SCPI(Driver):
    """Persistent TCP, serial or VISA connection with optional named operations."""
    def __init__(self, *args):
        super().__init__(*args)
        self.connection = None
        self.manager = None
        self.received = bytearray()
        self.expected_identity = None
        self.transmitted = False
        self.unconfirmed_change = self.config.get("unconfirmed_change", "fail")
        if self.unconfirmed_change not in ("fail", "hold"):
            raise ValueError("unconfirmed_change must be fail or hold")
        self.timeout = float(self.config.get("timeout", 5))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be positive")
        self.termination = self.config.get("termination", "\n")
        if not self.termination:
            raise ValueError("A text SCPI termination is required")
        self.protocol_source = None
        self._sync()
        self.last_sent = 0.0
        transport = self.config.get('transport', 'tcp')
        required = {'tcp': 'host', 'serial': 'port', 'visa': 'resource'}
        if transport not in required:
            raise ValueError('Unknown SCPI transport: ' + str(transport))
        if not isinstance(self.config.get(required[transport]), str) or not self.config[required[transport]].strip():
            raise ValueError('Specify the SCPI ' + required[transport])
        if transport == 'serial' and int(self.config.get('baudrate', 9600)) <= 0:
            raise ValueError('Baud rate must be positive')
        if 'source_address' in self.config:
            import ipaddress
            if not isinstance(self.config['source_address'], str):
                raise ValueError('source_address must be a local IP address string')
            ipaddress.ip_address(self.config['source_address'])
        self.max_block_bytes = self.config.get('max_block_bytes', 256 * 1024 * 1024)
        if type(self.max_block_bytes) is not int or self.max_block_bytes <= 0:
            raise ValueError('max_block_bytes must be a positive integer')
        if not isinstance(self.config.get('block_terminated', True), bool):
            raise ValueError('block_terminated must be true or false')
        # Result, reply and data descriptions are checked when the instrument is registered.
        self.arrays, self.writes = {}, {}
        self.readers, self.replies, self.finished = {}, {}, {}
        for name, spec in self.config.get('operations', {}).items():
            self._prepare(name, spec)

    def _sync(self):
        """Read protocol traits; a model description can add them after an identity query."""
        source = self.config.get('protocol', {})
        if source != self.protocol_source:
            # Traits of command languages that are not SCPI; SCPI instruments need none of them.
            self.protocol = text_protocol.protocol(source)
            self.protocol_source = copy.deepcopy(source)
            self.write_termination = self.protocol.get('write_termination', self.termination)
            self.read_termination = self.protocol.get('read_termination', self.termination)

    def _prepare(self, name, spec):
        """Check how one operation's replies and data are read; operations can arrive with a model description."""
        try:
            self.readers[name], self.replies[name], self.finished[name] = text_protocol.operation(spec)
            if spec.get('result', {}).get('type') == 'array':
                if 'query' not in spec:
                    raise ValueError('an array result needs a query')
                self.arrays[name] = arrays.check_result(spec['result'])
            if 'data' in spec:
                if 'write' not in spec:
                    raise ValueError('binary data is sent with a write command')
                self.writes[name] = arrays.check_data(spec['data'], spec.get('parameters', {}))
        except ValueError as error:
            raise ValueError(f'Operation {name}: {error}') from None

    def describe(self):
        self._sync()
        operations = {
            "identify":{"description":"Read SCPI identification", "effect":"observe", "parameters":{}, "query":"*IDN?"},
            "query":{"description":"Send a text query and read its response", "parameters":{"command":{"type":"string"}}},
            "write":{"description":"Send a command", "parameters":{"command":{"type":"string"}}},
        }
        if not self.protocol['identify']:
            del operations['identify']
        operations.update(self.config.get("operations", {}))
        connection = {k: self.config[k] for k in ('host', 'port', 'resource', 'backend', 'baudrate',
            'bytesize', 'parity', 'stopbits', 'xonxoff', 'rtscts', 'dsrdtr', 'timeout', 'termination', 'source_address',
            'unconfirmed_change', 'max_block_bytes', 'block_terminated', 'protocol') if k in self.config}
        return {"name":self.config.get("name", self.id), "mode":"hardware", "transport":self.config.get("transport", "tcp"), 'connection': connection,
                "operations":operations}

    def connect(self):
        if self.connection is not None:
            return
        kind = self.config.get("transport", "tcp")
        if kind == "tcp":
            source = self.config.get('source_address')
            self.connection = socket.create_connection((self.config["host"], int(self.config.get("port", 5025))),
                                                      self.timeout, source_address=(source, 0) if source else None)
            self.connection.settimeout(self.timeout)
        elif kind == "serial":
            import serial
            self.connection = serial.Serial(self.config["port"], int(self.config.get("baudrate", 9600)),
                                            timeout=self.timeout, write_timeout=self.timeout,
                                            bytesize=self.config.get('bytesize', 8), parity=self.config.get('parity', 'N'),
                                            stopbits=self.config.get('stopbits', 1), xonxoff=self.config.get('xonxoff', False),
                                            rtscts=self.config.get('rtscts', False), dsrdtr=self.config.get('dsrdtr', False))
        elif kind == "visa":
            import pyvisa
            backend = self.config.get("backend")
            self.manager = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
            self.connection = self.manager.open_resource(self.config["resource"])
            self.connection.timeout = self.timeout * 1000
            self.connection.read_termination = self.read_termination
            self.connection.write_termination = self.write_termination
        else:
            raise ValueError("Unknown SCPI transport: " + kind)
        if self.expected_identity:
            from .identification import parse_identity, verify_identity
            verify_identity(self.expected_identity, parse_identity(self.exchange('*IDN?', True)))

    def exchange(self, command, read=False):
        """Send one command; with read, return its reply line."""
        if "\n" in command or "\r" in command:
            raise ValueError("Use one text command per operation")
        try:
            self.connect()
            wait = self.protocol['min_interval'] - (time.monotonic() - self.last_sent)
            if wait > 0:
                time.sleep(wait)
            kind = self.config.get("transport", "tcp")
            self.transmitted = True
            if kind == "visa":
                self.connection.write(command)
            elif kind == "tcp":
                self.connection.sendall((command + self.write_termination).encode(self.config.get("encoding", "ascii")))
            else:
                self.connection.write((command + self.write_termination).encode(self.config.get("encoding", "ascii")))
            self.last_sent = time.monotonic()
            if self.protocol['echo']:
                echoed = self._line()
                if echoed.strip() != command.strip():
                    raise ValueError(f'Expected the echo of {command!r}; received {echoed!r}')
            return self._line() if read else None
        except Exception:
            self.close()
            raise

    def _line(self):
        """One reply line without its terminator."""
        kind = self.config.get("transport", "tcp")
        if kind == "visa":
            return self.connection.read().strip()
        terminator = self.read_termination.encode(self.config.get('encoding', 'ascii'))
        deadline = time.monotonic() + self.timeout
        search_from = 0
        while True:
            end = self.received.find(terminator, search_from)
            if end >= 0:
                response = bytes(self.received[:end])
                del self.received[:end + len(terminator)]
                if kind == 'tcp':
                    self.connection.settimeout(self.timeout)
                else:
                    self.connection.timeout = self.timeout
                return response.decode(self.config.get('encoding', 'ascii'))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Instrument response timed out")
            search_from = max(0, len(self.received) - len(terminator) + 1)
            if kind == 'tcp':
                self.connection.settimeout(remaining)
                chunk = self.connection.recv(65536)
            else:
                self.connection.timeout = remaining
                chunk = self.connection.read(min(65536, max(1, self.connection.in_waiting)))
            if not chunk:
                raise TimeoutError("Instrument returned no complete response")
            self.received.extend(chunk)
            if len(self.received) > 8 * 1024 * 1024:
                raise ValueError("Response exceeds text-query size")

    def _receive(self):
        """Read more bytes; raise after `timeout` seconds without data, so large blocks may take longer."""
        if self.config.get('transport', 'tcp') == 'tcp':
            self.connection.settimeout(self.timeout)
            chunk = self.connection.recv(65536)
            if not chunk:
                raise ConnectionError('Instrument closed the connection')
        else:
            self.connection.timeout = self.timeout
            chunk = self.connection.read(min(65536, max(1, self.connection.in_waiting)))
            if not chunk:
                raise TimeoutError('Instrument response timed out')
        self.received.extend(chunk)

    def exchange_block(self, command, payload=None):
        """Send a command followed by an IEEE 488.2 block, or query and return the block received."""
        if "\n" in command or "\r" in command:
            raise ValueError("Use one text command per operation")
        encoding = self.config.get('encoding', 'ascii')
        terminator = self.read_termination.encode(encoding)
        terminated = self.config.get('block_terminated', True)
        try:
            self.connect()
            kind = self.config.get('transport', 'tcp')
            if payload is not None:
                wire = command.encode(encoding) + arrays.block(payload) + self.write_termination.encode(encoding)
                self.transmitted = True
                if kind == 'tcp':
                    self.connection.sendall(wire)
                elif kind == 'serial':
                    self.connection.write(wire)
                else:
                    self.connection.write_raw(wire)
                return None
            if kind == 'visa':
                self.transmitted = True
                data = self.connection.query_binary_values(command, datatype='s', container=bytes,
                                                           expect_termination=terminated)
                if len(data) > self.max_block_bytes:
                    raise ValueError('Binary block exceeds max_block_bytes')
                return data
            wire = (command + self.write_termination).encode(encoding)
            self.transmitted = True
            if kind == 'tcp':
                self.connection.sendall(wire)
            else:
                self.connection.write(wire)
            while (parsed := arrays.parse_block(self.received)) is None:
                if len(self.received) > self.max_block_bytes + 16:
                    raise ValueError('Binary block exceeds max_block_bytes')
                self._receive()
            data, end = parsed
            if len(data) > self.max_block_bytes:
                raise ValueError('Binary block exceeds max_block_bytes')
            del self.received[:end]
            if terminated:
                while len(self.received) < len(terminator) and terminator.startswith(bytes(self.received)):
                    self._receive()
                if not self.received.startswith(terminator):
                    raise ValueError('Expected the response terminator after the binary block')
                del self.received[:len(terminator)]
            return data
        except Exception:
            self.close()
            raise

    def invoke(self, operation, arguments, cancelled):
        if cancelled.is_set():
            raise CancelledError()
        spec = self.describe()["operations"][operation]
        if operation not in self.readers:
            self._prepare(operation, spec)
        is_query = operation == "query" or "query" in spec
        template = spec.get("query", spec.get("write"))
        command = template.format_map(arguments) if template else arguments["command"]
        completion = spec.get("completion")
        array_spec, data_spec = self.arrays.get(operation), self.writes.get(operation)
        payload = None
        if data_spec:
            payload = arrays.encode(arguments[data_spec['parameter']], data_spec['dtype'], data_spec['byte_order'])
        # Identity verification on a new connection is not this operation's command.
        try:
            self.connect()
        except Exception:
            self.close()
            raise
        self.transmitted = False
        # A write is acknowledged when the operation or the protocol says so.
        expected = self.replies.get(operation)
        if expected is None:
            expected = self.protocol['write_reply']
        try:
            if payload is not None:
                value = self.exchange_block(command, payload)
            elif array_spec and array_spec['encoding'] == 'block':
                value = self.exchange_block(command)
            else:
                value = self.exchange(command, is_query or expected is not False)
                self._accept(command, value, None if is_query else expected)
            if completion:
                deadline = time.monotonic() + float(completion.get("timeout", 30))
                finished = self.finished.get(operation)
                while True:
                    query = completion["query"].format_map(arguments)
                    observed = self.exchange(query, True)
                    self._accept(query, observed)
                    if finished.search(observed) if finished else observed == str(completion.get("equals", "1")):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Completion response did not arrive")
                    if cancelled.wait(float(completion.get("interval", 0.1))):
                        raise CancelledError('Operation cancelled')
        except (CancelledError, Refused):
            raise
        except Exception as error:
            # Once a change command has left this computer, a lost reply does not show
            # whether the instrument acted. Optionally keep that outcome open.
            if (self.unconfirmed_change == "hold" and self.transmitted
                    and spec.get("effect", "change") != "observe"):
                raise UnconfirmedChange(
                    f"{command!r} was sent; completion was not confirmed ({type(error).__name__}: {error})") from error
            raise
        result = {"time":now(), "source":"device" if is_query else "driver", "command":command}
        if array_spec:
            if array_spec['encoding'] == 'ascii':
                value = arrays.encode(arrays.parse_ascii(value, array_spec['separator']), 'float64', array_spec['byte_order'])
            result.update(arrays.result(value, array_spec, cancelled, operation))
        elif is_query:
            encoding = spec.get("result", {})
            reader = self.readers.get(operation)
            result["value"] = reader(value) if reader else value
            if operation == 'identify':
                from .identification import parse_identity
                try:
                    result['identity'] = parse_identity(value)
                except ValueError as error:
                    result['identity_error'] = str(error)
            if "unit" in encoding:
                result["unit"] = encoding["unit"]
        else:
            result["sent"] = True
            if payload is not None:
                result["data_bytes"] = len(payload)
            elif value is not None:
                result["reply"] = value
        if completion:
            result["completion_response"] = observed
        return result

    def _accept(self, command, reply, expected=None):
        """Fail when the instrument answered with an error; it did not act, so nothing is unconfirmed."""
        if reply is None:
            return
        error = self.protocol['error_reply']
        if error and error.search(reply):
            raise Refused(f'The instrument answered {command!r} with {reply!r}')
        if expected not in (None, True, False) and not expected.search(reply):
            raise Refused(f'The instrument answered {command!r} with {reply!r}; expected {expected.pattern!r}')

    def close(self):
        self.received.clear()
        for obj in (self.connection, self.manager):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self.connection = self.manager = None


def make_driver(record, registry):
    execution = record.get('execution', {})
    if execution and record.get('driver') == 'manual':
        raise ValueError('Manual steps wait in the service; remove execution')
    if execution:
        if execution.get('mode') != 'process' or record.get('driver') == 'process':
            raise ValueError('Use execution.mode=process for an installed instrument driver')
        from .process_driver import ProcessDriver
        local = {k: v for k, v in record.items() if k != 'execution'}
        return ProcessDriver(record['id'], {k: v for k, v in execution.items() if k != 'mode'} |
                             {'configuration': {'devices': [local]}}, registry)
    if record.get('driver') == 'process':
        from .process_driver import ProcessDriver
        return ProcessDriver(record['id'], record.get('config', {}), registry)
    name = record["driver"]
    from .adapters import Modbus, HTTP
    from .simulation import Laboratory
    from .software import Command
    builtins = {"simulated":Simulated, "simulated-lab":Laboratory, "scpi":SCPI, "modbus":Modbus, "http":HTTP, "command":Command}
    if name in builtins:
        cls = builtins[name]
    elif name == 'qcodes':
        from .qcodes_driver import QCoDeS
        cls = QCoDeS
    elif name == 'manual':
        from .manual import Manual
        cls = Manual
    elif name == 'opcua':
        from .opcua_driver import OPCUA
        cls = OPCUA
    elif name == 'epics':
        from .epics_driver import EPICS
        cls = EPICS
    elif name == 'python':
        from .python_driver import PythonObject
        cls = PythonObject
    else:
        module, separator, class_name = name.partition(":")
        if not separator:
            from importlib.metadata import entry_points
            matches = list(entry_points(group='ai4instruments.drivers', name=name))
            if len(matches) != 1:
                raise ValueError('Driver must name a built-in adapter, installed driver entry point or module:Class')
            cls = matches[0].load()
        else:
            cls = getattr(importlib.import_module(module), class_name)
    driver = cls(record["id"], record.get("config", {}), registry)
    if name == 'scpi':
        driver.expected_identity = record.get('identity')
    return driver
