"""What a device record contains for each built-in adapter.

An agent or a person adding an instrument reads one entry: the ``config`` fields, the fields of an
operation, and a short example to adapt from the instrument's manual. Entries stay terse; the
manual pages in ``docs/runtime/drivers.md`` explain behavior.
"""

PARAMETERS = ('name -> {type: number | integer | string | boolean | array | object, minimum, maximum, enum, default, '
              'required, unit}; values outside are refused before the instrument is called')
COMMON = {
    'parameters': PARAMETERS,
    'effect': '"observe" for a read that changes nothing; omitted, the operation counts as a change',
    'description': 'what the operation does',
}
RECORD = {
    'id': 'device name used in experiments',
    'driver': 'adapter name',
    'config': 'the fields below',
    'resources': 'names of shared physical objects; operations on devices sharing one do not overlap',
    'allowed_operations': 'name patterns to keep; omit to keep all',
    'calibration': '{id, date, valid_until, certificate, uncertainty, enforce}',
    'monitors': '[{id, operation, interval_s, minimum, maximum, equals, on_violation: record | hold, hold: [resources]}]',
    'execution': '{"mode": "process"} runs the driver in its own process',
}
ARRAY = ('{type: "array", dtype: int8..uint64 | float32 | float64, byte_order, shape, unit, axes}: saved as a file and '
         'described under data; values are returned inline up to inline_limit')

REFERENCE = {
    'scpi': {
        'use': 'Instruments exchanging text lines over TCP, serial or VISA: SCPI, and other command languages with protocol',
        'config': {
            'transport': 'tcp (host, port default 5025) | serial (port, baudrate, bytesize, parity, stopbits) | visa (resource, backend)',
            'timeout': 'seconds, default 5',
            'termination': r'line ending of commands and replies, default "\n"',
            'unconfirmed_change': '"hold": a change that was sent without its reply or completion holds the device',
            'protocol': ('for commands that are not SCPI: {read_termination, write_termination, identify: false without *IDN?, '
                         'error_reply: pattern, write_reply: true | {match}, echo, min_interval}'),
            'operations': 'name -> operation',
        },
        'operation': {
            'query': 'command whose reply is the result; {name} inserts a parameter',
            'write': 'command without a result; exactly one of query or write',
            'result': ('{type: number | integer | boolean | string, unit, match: pattern whose first group is the value, '
                       'scale, offset, map: {reply: value}}, or ' + ARRAY + ' with encoding block | ascii'),
            'completion': '{query, equals | match, interval, timeout}: asked until the reply agrees',
            'reply': 'on a write: true | false | {match} when the instrument acknowledges it with one line',
            'data': '{parameter, dtype, byte_order}: that array parameter is sent as a binary block after the write',
        },
        'builtin': 'identify (*IDN?), query and write with a command argument',
        'example': {'id': 'power-meter', 'driver': 'scpi', 'config': {
            'transport': 'tcp', 'host': '192.168.1.50', 'port': 5025, 'operations': {
                'read_power': {'query': 'MEAS:POW?', 'effect': 'observe', 'result': {'type': 'number', 'unit': 'W'}},
                'set_wavelength': {'write': 'SENS:CORR:WAV {nm}',
                                   'parameters': {'nm': {'type': 'number', 'minimum': 400, 'maximum': 1100, 'unit': 'nm'}},
                                   'completion': {'query': '*OPC?', 'equals': '1', 'timeout': 30}}}}},
    },
    'modbus': {
        'use': 'Registers and coils of controllers, power supplies, sensors and PLCs over Modbus TCP or RTU',
        'config': {
            'transport': 'tcp (host, port default 502) | serial (port, baudrate, bytesize, parity, stopbits)',
            'device_id': 'unit address, default 1; devices on one bus share its connection',
            'timeout': 'seconds, default 3',
            'operations': 'name -> operation',
        },
        'operation': {
            'function': ('read_holding_registers | read_input_registers | read_coils | read_discrete_inputs | write_register | '
                         'write_registers | write_coil'),
            'arguments': '{address, count}: zero-based protocol address',
            'type': 'uint16 (default) | int16 | uint32 | int32 | float32 | uint64 | int64 | float64',
            'word_order': 'big (default, high word first) | little, for values wider than one register',
            'byte_order': 'big (default) | little, within a register',
            'scale': 'value = register value x scale + offset',
            'offset': 'see scale',
            'unit': 'unit of the value',
            'value_parameter': 'on a write: the parameter holding the physical value',
        },
        'builtin': 'the functions above with address, count or value arguments',
        'example': {'id': 'temperature-controller', 'driver': 'modbus', 'config': {
            'transport': 'tcp', 'host': '192.168.1.60', 'device_id': 1, 'operations': {
                'read_temperature': {'function': 'read_holding_registers', 'arguments': {'address': 0}, 'type': 'float32',
                                     'unit': 'degC', 'effect': 'observe'},
                'set_temperature': {'function': 'write_register', 'arguments': {'address': 2}, 'value_parameter': 'celsius',
                                    'scale': 0.1, 'parameters': {'celsius': {'type': 'number', 'minimum': 0, 'maximum': 100,
                                                                             'unit': 'degC'}}}}}},
    },
    'http': {
        'use': 'Instruments and device servers with a JSON HTTP interface',
        'config': {'base_url': 'address of the service', 'headers': 'sent with every request', 'timeout': 'seconds, default 10',
                   'operations': 'name -> operation'},
        'operation': {
            'method': 'GET (default) | POST | PUT | PATCH | DELETE',
            'path': 'path below base_url; {name} inserts a parameter',
            'query': 'query fields; "${name}" inserts a parameter with its JSON type',
            'body': 'JSON body; "${name}" inserts a parameter with its JSON type',
            'result_path': 'keys and indices leading to the value in the response',
            'unit': 'unit of the value',
        },
        'example': {'id': 'environment-sensor', 'driver': 'http', 'config': {
            'base_url': 'http://192.168.1.70:8080', 'operations': {
                'read_temperature': {'method': 'GET', 'path': '/temperature', 'result_path': ['temperature'], 'unit': 'K',
                                     'effect': 'observe'},
                'set_interval': {'method': 'PUT', 'path': '/settings', 'body': {'interval_s': '${seconds}'},
                                 'parameters': {'seconds': {'type': 'number', 'minimum': 0.1, 'maximum': 60}}}}}},
    },
    'opcua': {
        'use': 'OPC UA servers: controllers, process equipment, LADS analytical instruments (opcua extra)',
        'config': {
            'endpoint': 'opc.tcp://host:port/path',
            'nodes': 'NodeIds of the objects to use, such as "ns=2;s=Reactor"; default every vendor object',
            'depth': 'object levels walked below each node, default 6',
            'username': 'with password_env: the service environment variable holding the password',
            'password_env': 'see username',
            'security': 'policy,mode,certificate,key',
            'timeout': 'seconds, default 5',
            'unconfirmed_change': '"hold": a write or call sent without a reply holds the device',
        },
        'operation': {},
        'builtin': ('operations come from the server: get.<path>, set.<path> with a value argument, call.<path>, snapshot; '
                    'read them with describe_instrument after registering'),
        'example': {'id': 'reactor', 'driver': 'opcua', 'config': {'endpoint': 'opc.tcp://192.168.1.50:4840',
                                                                  'nodes': ['ns=2;s=Reactor']}},
    },
    'epics': {
        'use': 'EPICS Channel Access process variables of IOCs (epics extra)',
        'config': {
            'pvs': 'PV names, or {name, wait: false, put_timeout} objects',
            'prefix': 'prepended to each name; operation names omit it',
            'timeout': 'connection and read timeout in seconds, default 5',
            'put_timeout': 'longest wait for put completion in seconds, default 300',
            'unconfirmed_change': '"hold": a put that does not complete in time holds the device',
        },
        'operation': {},
        'builtin': 'get.<name> and, with write access, set.<name> with a value argument; snapshot; a set waits for put completion',
        'example': {'id': 'sample-stage', 'driver': 'epics', 'config': {'prefix': 'BL7:SMP:', 'pvs': ['X.VAL', 'X.RBV']}},
    },
    'qcodes': {
        'use': 'Instruments with a QCoDeS driver class (qcodes extra)',
        'config': {
            'instrument': 'driver class as module:Class',
            'address': 'VISA address passed to the driver',
            'arguments': 'other keyword arguments of the driver class',
            'parameters': 'patterns over parameter paths to expose, default all',
            'exclude': 'patterns to leave out',
        },
        'operation': {},
        'builtin': 'get.<parameter> and set.<parameter> with a value argument, with ranges and units from the driver; snapshot',
        'example': {'id': 'source', 'driver': 'qcodes', 'execution': {'mode': 'process'}, 'config': {
            'instrument': 'qcodes.instrument_drivers.yokogawa:YokogawaGS200', 'address': 'TCPIP0::192.168.1.80::inst0::INSTR',
            'parameters': ['voltage', 'current', 'output']}},
    },
    'python': {
        'use': 'An object of an installed Python package: pylablib, PyMeasure, InstrumentKit or a vendor SDK',
        'config': {
            'object': 'module:Class or a factory, called with arguments and keywords',
            'arguments': 'positional arguments for creating the object',
            'keywords': 'keyword arguments for creating the object',
            'context': 'true to enter the object as a context manager',
            'close': 'method called when the device closes, default close',
            'unconfirmed_change': '"hold": a change that raises anything but an argument error holds the device',
            'operations': 'name -> operation',
        },
        'operation': {
            'call': 'method, as a dotted path from the object; parameters are passed as keyword arguments',
            'get': 'attribute to read; exactly one of call, get or set',
            'set': 'attribute written with the single parameter',
            'positional': 'parameters passed by position, in call order',
            'fixed': 'constant keyword arguments',
            'completion': '{call | get, equals, interval, timeout}: read until it equals the value',
            'on_cancel': '{call}: made when the wait is cancelled, such as stop',
            'result': '{unit, path: keys and indices into the returned value, items: {name: {path, unit}}}; NumPy arrays are saved as files',
        },
        'example': {'id': 'delay-stage', 'driver': 'python', 'execution': {'mode': 'process'}, 'config': {
            'object': 'pylablib.devices.Thorlabs:KinesisMotor', 'arguments': ['27000001'], 'keywords': {'scale': 'stage'},
            'operations': {
                'move_to': {'call': 'move_to', 'parameters': {'position': {'type': 'number', 'minimum': 0, 'maximum': 0.025,
                                                                           'unit': 'm'}},
                            'completion': {'call': 'is_moving', 'equals': False, 'timeout': 60}, 'on_cancel': {'call': 'stop'}},
                'position': {'call': 'get_position', 'effect': 'observe', 'result': {'unit': 'm'}}}}},
    },
    'manual': {
        'use': 'Steps a person performs: mounting a sample, aligning by eye, reading a gauge',
        'config': {'operations': 'name -> operation'},
        'operation': {
            'instruction': 'what the person does; {name} inserts a parameter',
            'readings': 'values the person reports, described like parameters',
        },
        'builtin': 'a running step waits in manual_tasks until report_manual_step records done, failed or unsure',
        'example': {'id': 'bench', 'driver': 'manual', 'config': {'operations': {
            'load_sample': {'instruction': 'Mount sample {sample} in holder A and close the lid',
                            'parameters': {'sample': {'type': 'string'}},
                            'readings': {'mass_g': {'type': 'number', 'minimum': 0, 'unit': 'g'}}}}}},
    },
    'command': {
        'use': 'Programs started once per operation: solvers, analysis scripts, vendor command-line tools',
        'config': {
            'cwd': 'working directory', 'env': 'environment variables', 'mode': '"software" for computation',
            'concurrency': 'operations that may run at once', 'requirements': 'named capacities each operation uses, such as {cpu: 1}',
            'timeout': 'seconds; 0 for no limit', 'operations': 'name -> operation',
        },
        'operation': {
            'command': ('executable and arguments, without a shell; {python} is the service interpreter, {request}, {output} and '
                        '{result} are paths, "${name}" passes a parameter'),
        },
        'builtin': 'the program reads IOP_ARGUMENTS_JSON and writes its JSON result to IOP_RESULT_FILE; files in IOP_OUTPUT_DIR are kept',
        'example': {'id': 'processor', 'driver': 'command', 'config': {'mode': 'software', 'cwd': '/path/to/program', 'operations': {
            'calculate': {'command': ['{python}', 'calculate.py'], 'parameters': {'x': {'type': 'number'}}}}}},
    },
}


def reference(name):
    """The entry for one adapter, with the fields every device record and operation share."""
    if name not in REFERENCE:
        raise KeyError(name)
    entry = REFERENCE[name]
    # Adapters that read their operations from the instrument have none to describe.
    operation = {**entry['operation'], **COMMON} if entry['operation'] else {}
    return {'name': name, **entry, 'operation': operation, 'record': RECORD}
