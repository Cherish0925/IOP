"""OPC UA servers: industrial controllers, process equipment and LADS analytical instruments.

Variables under the configured nodes become ``get.<path>`` (observe) and, when writable,
``set.<path>`` operations; methods become ``call.<path>``. Types, ranges (EURange), units
(EngineeringUnits) and enumerations come from the server, so the Runtime checks arguments before a
request is sent. ``snapshot`` reads every scalar variable together.
"""
import base64
import dataclasses
import datetime
import enum
import math
import os
import struct
from . import arrays
from .drivers import Driver, UnconfirmedChange
from .store import now

INTEGERS = {'SByte': ('int8', -2**7, 2**7 - 1), 'Byte': ('uint8', 0, 2**8 - 1),
            'Int16': ('int16', -2**15, 2**15 - 1), 'UInt16': ('uint16', 0, 2**16 - 1),
            'Int32': ('int32', -2**31, 2**31 - 1), 'UInt32': ('uint32', 0, 2**32 - 1),
            'Int64': ('int64', -2**63, 2**63 - 1), 'UInt64': ('uint64', 0, 2**64 - 1)}
FLOATS = {'Float': 'float32', 'Double': 'float64'}
PACK = {'int8': 'b', 'uint8': 'B', 'int16': 'h', 'uint16': 'H', 'int32': 'i', 'uint32': 'I',
        'int64': 'q', 'uint64': 'Q', 'float32': 'f', 'float64': 'd'}
FLOAT32 = 3.4028234663852886e38
METADATA = ('EURange', 'EngineeringUnits', 'EnumStrings', 'EnumValues', 'InputArguments', 'OutputArguments')
# Status codes meaning the session or connection is gone; the request was not processed.
DISCONNECTED = {'BadSessionIdInvalid', 'BadSessionClosed', 'BadSessionNotActivated', 'BadSecureChannelClosed',
                'BadSecureChannelIdInvalid', 'BadConnectionClosed', 'BadNotConnected', 'BadServerNotConnected'}
LIMIT = 2000
BATCH = 500


def segment(name):
    """One path component: dots and white space would be read as structure."""
    return '_'.join(str(name).replace('.', '_').split())


def text(value):
    return (getattr(value, 'Text', value) or '') if value is not None else ''


def plain(value):
    """JSON form of a value read from the server."""
    if isinstance(value, enum.Enum):
        return value.value if isinstance(value.value, (int, str)) else value.name
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return {'base64': base64.b64encode(value).decode()}
    if hasattr(value, 'Text') and hasattr(value, 'Locale'):
        return value.Text
    if dataclasses.is_dataclass(value):
        return {item.name: plain(getattr(value, item.name)) for item in dataclasses.fields(value)}
    if hasattr(value, 'to_string'):
        return value.to_string()
    return str(value)


def field(variant, rank, properties):
    """Parameter description for writing a value of this type, or None when IOP cannot write it."""
    name = getattr(variant, 'name', '')
    if name == 'Boolean':
        result = {'type': 'boolean'}
    elif name in INTEGERS:
        result = {'type': 'integer', 'minimum': INTEGERS[name][1], 'maximum': INTEGERS[name][2]}
    elif name == 'Float':
        result = {'type': 'number', 'minimum': -FLOAT32, 'maximum': FLOAT32}
    elif name == 'Double':
        result = {'type': 'number'}
    elif name == 'String':
        result = {'type': 'string'}
    else:
        return None
    limits = properties.get('EURange')
    if limits is not None and result['type'] != 'boolean' and result['type'] != 'string':
        low, high = limits.Low, limits.High
        if math.isfinite(low) and math.isfinite(high) and low <= high:
            if result['type'] == 'integer':
                low, high = math.ceil(low), math.floor(high)
            result['minimum'] = max(result.get('minimum', low), low)
            result['maximum'] = min(result.get('maximum', high), high)
    labels = None
    if result['type'] == 'integer' and properties.get('EnumStrings'):
        labels = {index: text(label) for index, label in enumerate(properties['EnumStrings'])}
    elif result['type'] == 'integer' and properties.get('EnumValues'):
        labels = {item.Value: text(item.DisplayName) for item in properties['EnumValues']}
    if labels:
        result['enum'] = list(labels)
        result['description'] = ', '.join(f'{value}: {label}' for value, label in labels.items())
    if rank >= 0:
        result = {'type': 'array', 'items': result}
    unit = text(getattr(properties.get('EngineeringUnits'), 'DisplayName', None))
    if unit:
        result['unit'] = unit
    return result


def flatten(value):
    """Return (values, shape) for a regular nested list of numbers, or None."""
    shape = []
    probe = value
    while isinstance(probe, list):
        if not probe:
            return None
        shape.append(len(probe))
        probe = probe[0]
    flat = value
    for _ in shape[1:]:
        if any(not isinstance(row, list) or len(row) != len(flat[0]) for row in flat):
            return None
        flat = [item for row in flat for item in row]
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in flat):
        return None
    return flat, shape


class OPCUA(Driver):
    """Config: endpoint (opc.tcp://host:port/path), optional nodes (NodeId strings), depth, timeout,
    username with password_env, security (asyncua security string) and unconfirmed_change."""

    def __init__(self, *args):
        super().__init__(*args)
        from asyncua import ua
        endpoint = self.config.get('endpoint')
        if not isinstance(endpoint, str) or not endpoint.startswith('opc.tcp://'):
            raise ValueError('Set endpoint to the server address, such as opc.tcp://192.168.1.20:4840')
        nodes = self.config.get('nodes', [])
        if not isinstance(nodes, list) or any(not isinstance(node, str) for node in nodes):
            raise ValueError('nodes must be a list of NodeId strings such as ns=2;s=Reactor')
        self.roots = [ua.NodeId.from_string(node) for node in nodes]
        self.depth = self.config.get('depth', 6)
        if type(self.depth) is not int or not 1 <= self.depth <= 20:
            raise ValueError('depth must be an integer from 1 to 20')
        self.timeout = float(self.config.get('timeout', 5))
        if not 0 < self.timeout <= 300:
            raise ValueError('timeout must be between 0 and 300 seconds')
        self.unconfirmed = self.config.get('unconfirmed_change', 'fail')
        if self.unconfirmed not in ('fail', 'hold'):
            raise ValueError('unconfirmed_change must be fail or hold')
        if 'password' in self.config:
            raise ValueError('Put the password in an environment variable of the service and name it in password_env')
        self.client = None
        self.types = {}
        self.batch = {'read': BATCH, 'browse': BATCH}
        try:
            self._connect()
            self.variables, self.methods, self.unsupported = self._browse()
            self.identity = self._identity()
        except BaseException:
            self.close()
            raise

    # Connection

    def _connect(self):
        if self.client is not None:
            return self.client
        from asyncua.sync import Client, ThreadLoop
        loop = ThreadLoop(self.timeout * 4 + 10)
        loop.daemon = True
        loop.start()
        client = Client(self.config['endpoint'], timeout=self.timeout, tloop=loop)
        try:
            if self.config.get('username'):
                client.set_user(self.config['username'])
                variable = self.config.get('password_env')
                if variable:
                    if variable not in os.environ:
                        raise ValueError(f'Environment variable {variable} holding the password is not set')
                    client.set_password(os.environ[variable])
            if self.config.get('security'):
                client.set_security_string(self.config['security'])
            client.connect()
        except BaseException:
            loop.stop()
            raise
        self.client, self.loop = client, loop
        return client

    def close(self):
        client, self.client = self.client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        loop = getattr(self, 'loop', None)
        if loop is not None and loop.is_alive():
            loop.stop()
        self.loop = None

    # Address space

    def _variant(self, datatype):
        from asyncua import ua
        from asyncua.sync import data_type_to_variant_type
        if datatype.NamespaceIndex == 0 and isinstance(datatype.Identifier, int) and datatype.Identifier <= 25:
            return ua.VariantType(datatype.Identifier)
        key = datatype.to_string()
        if key not in self.types:
            self.types[key] = data_type_to_variant_type(self.client.get_node(datatype))
        return self.types[key]

    def _read(self, items):
        """Read (NodeId, attribute) pairs in few requests; unreadable values are None."""
        from asyncua import ua
        values, size = [], self.batch['read']
        for start in range(0, len(items), size):
            parameters = ua.ReadParameters()
            parameters.NodesToRead = [ua.ReadValueId(NodeId=node, AttributeId=attribute)
                                      for node, attribute in items[start:start + size]]
            for data in self.loop.post(self.client.aio_obj.uaclient.read(parameters)):
                good = data.Value is not None and (data.StatusCode is None or data.StatusCode.is_good())
                values.append(data.Value.Value if good else None)
        return values

    def _references(self, nodes, reference):
        """Forward references of many nodes, following continuation points."""
        from asyncua import ua
        found, size = [], self.batch['browse']
        for start in range(0, len(nodes), size):
            parameters = ua.BrowseParameters()
            parameters.NodesToBrowse = [ua.BrowseDescription(
                NodeId=node, BrowseDirection=ua.BrowseDirection.Forward, ReferenceTypeId=ua.NodeId(reference),
                IncludeSubtypes=True, NodeClassMask=0, ResultMask=ua.BrowseResultMask.All)
                for node in nodes[start:start + size]]
            for result in self.loop.post(self.client.aio_obj.uaclient.browse(parameters)):
                references, point = list(result.References or []), result.ContinuationPoint
                while point:
                    following = ua.BrowseNextParameters()
                    following.ReleaseContinuationPoints, following.ContinuationPoints = False, [point]
                    result = self.loop.post(self.client.aio_obj.uaclient.browse_next(following))[0]
                    references += result.References or []
                    point = result.ContinuationPoint
                found.append(references)
        return found

    def _properties(self, nodes):
        """The metadata properties (ranges, units, enumerations, arguments) of many nodes."""
        from asyncua import ua
        wanted = [(index, reference) for index, references in enumerate(self._references(nodes, ua.ObjectIds.HasProperty))
                  for reference in references if reference.BrowseName.Name in METADATA]
        values = self._read([(reference.NodeId, ua.AttributeIds.Value) for _, reference in wanted])
        properties = [{} for _ in nodes]
        for (index, reference), value in zip(wanted, values):
            properties[index][reference.BrowseName.Name] = value
        return properties

    def _browse(self):
        """Walk the selected nodes one level at a time, with one browse and one read per level."""
        from asyncua import ua
        limits = ua.ObjectIds.Server_ServerCapabilities_OperationLimits_MaxNodesPerRead, \
            ua.ObjectIds.Server_ServerCapabilities_OperationLimits_MaxNodesPerBrowse
        # Servers state how many nodes one request may carry; 0 or absent means no stated limit.
        for key, limit in zip(('read', 'browse'), self._read([(ua.NodeId(node), ua.AttributeIds.Value) for node in limits])):
            if isinstance(limit, int) and limit > 0:
                self.batch[key] = min(BATCH, limit)
        if self.roots:
            names = self._read([(node, ua.AttributeIds.BrowseName) for node in self.roots])
            if None in names:
                raise ValueError('Node not found: ' + self.roots[names.index(None)].to_string())
            level = [(node, segment(name.Name), (index,)) for index, (node, name) in enumerate(zip(self.roots, names))]
        else:
            level = [(r.NodeId, segment(r.BrowseName.Name), (index,)) for index, r in
                     enumerate(self.client.nodes.objects.get_children_descriptions())
                     if r.NodeId.NamespaceIndex != 0 and r.NodeClass == ua.NodeClass.Object]
        found, seen = {}, {node.to_string() for node, _, _ in level}
        for depth in range(1, self.depth + 1):
            variables, methods, objects = [], [], []
            for (node, path, order), references in zip(level, self._references(
                    [node for node, _, _ in level], ua.ObjectIds.HierarchicalReferences)):
                for index, reference in enumerate(references):
                    key = reference.NodeId.to_string()
                    if key in seen:
                        continue
                    seen.add(key)
                    child = (reference.NodeId, path + '.' + segment(reference.BrowseName.Name), order + (index,))
                    if reference.NodeClass == ua.NodeClass.Variable:
                        variables.append(child)
                    elif reference.NodeClass == ua.NodeClass.Method:
                        methods.append(child + (node,))
                    elif reference.NodeClass == ua.NodeClass.Object and depth < self.depth:
                        objects.append(child)
            if len(found) + len(variables) + len(methods) > LIMIT:
                raise ValueError(f'The selected nodes hold more than {LIMIT} variables and methods; list narrower nodes')
            for path, entry in self._variables(variables) + self._methods(methods):
                if path in found:
                    raise ValueError(f'Two nodes have the path {path}; list their parents separately in nodes')
                found[path] = entry
            level = objects
        if not found:
            raise ValueError('No variables or methods were found under the selected nodes')
        # Server order, depth first: the order in which an object's children were browsed.
        ordered = sorted(found.items(), key=lambda item: item[1]['order'])
        variables = {path: entry for path, entry in ordered if entry['kind'] == 'variable'}
        methods = {path: entry for path, entry in ordered if entry['kind'] == 'method'}
        unsupported = [prefix + path for path, entry in ordered for prefix in entry['unsupported']]
        return variables, methods, unsupported

    def _variables(self, found):
        from asyncua import ua
        attributes = (ua.AttributeIds.DataType, ua.AttributeIds.ValueRank, ua.AttributeIds.AccessLevel,
                      ua.AttributeIds.UserAccessLevel, ua.AttributeIds.Description)
        values = self._read([(node, attribute) for node, _, _ in found for attribute in attributes])
        readable = []
        for number, (node, path, order) in enumerate(found):
            datatype, rank, level, user, description = values[number * 5:number * 5 + 5]
            access = user if user is not None else level or 0
            if datatype is not None and access & ua.AccessLevel.CurrentRead.mask:
                readable.append((node, path, order, datatype, -1 if rank is None else rank, access, text(description)))
        entries = []
        for (node, path, order, datatype, rank, access, description), properties in zip(
                readable, self._properties([item[0] for item in readable])):
            variant = self._variant(datatype)
            if variant == ua.VariantType.Variant:
                # Abstract types such as Number are written with the type of the current value.
                variant = self.client.get_node(node).read_data_value().Value.VariantType
            writable = access & ua.AccessLevel.CurrentWrite.mask
            write = field(variant, rank, properties) if writable else None
            entries.append((path, {'kind': 'variable', 'order': order, 'id': node, 'variant': variant, 'rank': rank,
                                   'write': write, 'unsupported': ['set.'] if writable and write is None else [],
                                   'unit': text(getattr(properties.get('EngineeringUnits'), 'DisplayName', None)),
                                   'description': description}))
        return entries

    def _methods(self, found):
        from asyncua import ua
        descriptions = self._read([(node, ua.AttributeIds.Description) for node, _, _, _ in found])
        entries = []
        for (node, path, order, parent), description, properties in zip(
                found, descriptions, self._properties([item[0] for item in found])):
            entry = {'kind': 'method', 'order': order, 'id': node, 'parent': parent, 'inputs': [], 'unsupported': [],
                     'outputs': [argument.Name for argument in properties.get('OutputArguments') or []],
                     'description': text(description)}
            for argument in properties.get('InputArguments') or []:
                variant = self._variant(argument.DataType)
                spec = field(variant, argument.ValueRank, {})
                if spec is None or not argument.Name:
                    entry['unsupported'] = ['call.']
                    break
                if text(argument.Description):
                    spec['description'] = text(argument.Description)
                entry['inputs'].append((argument.Name, variant, spec))
            entries.append((path, entry))
        return entries

    def _identity(self):
        from asyncua import ua
        info = self.client.get_node(ua.ObjectIds.Server_ServerStatus_BuildInfo).read_value()
        identity = {'manufacturer': info.ManufacturerName, 'model': info.ProductName, 'firmware': info.SoftwareVersion}
        if len(self.roots) == 1:
            # Devices following the OPC UA Device Integration model, such as LADS instruments.
            names = {'Manufacturer': 'manufacturer', 'Model': 'model', 'SerialNumber': 'serial_number',
                     'SoftwareRevision': 'firmware'}
            found = [r for r in self.client.get_node(self.roots[0]).get_children_descriptions() if r.BrowseName.Name in names]
            values = self.client.read_values([self.client.get_node(r.NodeId) for r in found]) if found else []
            device = {names[r.BrowseName.Name]: text(value) for r, value in zip(found, values) if text(value)}
            if device.get('manufacturer') and device.get('model'):
                identity = device
        return {key: str(value) for key, value in identity.items() if value}

    def describe(self):
        operations = {}
        scalars = [path for path, entry in self.variables.items() if entry['rank'] < 0]
        if scalars:
            operations['snapshot'] = {'description': 'Read every scalar variable together', 'effect': 'observe',
                                      'parameters': {}}
        for path, entry in self.variables.items():
            result = {'unit': entry['unit']} if entry['unit'] else {}
            operations['get.' + path] = {**({'description': entry['description']} if entry['description'] else {}),
                                         'effect': 'observe', 'parameters': {}, **({'result': result} if result else {})}
            if entry['write']:
                operations['set.' + path] = {**({'description': entry['description']} if entry['description'] else {}),
                                             'parameters': {'value': entry['write']}}
        for path, entry in self.methods.items():
            if entry['unsupported']:
                continue
            operations['call.' + path] = {**({'description': entry['description']} if entry['description'] else {}),
                                          'parameters': {name: spec for name, _, spec in entry['inputs']}}
        description = {'name': self.config.get('name', self.id), 'mode': 'hardware', 'operations': operations,
                       'connection': {'endpoint': self.config['endpoint'], 'unconfirmed_change': self.unconfirmed},
                       'identity': self.identity}
        if self.unsupported:
            description['unsupported'] = self.unsupported
        return description

    # Operations

    def _request(self, operation, change, call):
        """Send one request. A failure after it may have reached the server is reported as unconfirmed."""
        from asyncua import ua
        client = self._connect()
        try:
            return call(client)
        except ua.UaStatusCodeError as error:
            # The server answered: the request was refused or failed there.
            if ua.StatusCode(error.code).name in DISCONNECTED:
                self.close()
            raise RuntimeError(f'{operation}: {error}') from None
        except (ValueError, TypeError, OverflowError, struct.error) as error:
            raise ValueError(f'{operation}: {error}') from None
        except Exception as error:
            self.close()
            if change and self.unconfirmed == 'hold':
                raise UnconfirmedChange(f'{operation} was sent and no reply arrived: {type(error).__name__} {error}') from None
            raise RuntimeError(f'{operation}: no reply from the server ({type(error).__name__} {error})') from None

    def invoke(self, operation, arguments, cancelled):
        from asyncua import ua
        kind, _, path = operation.partition('.')
        if operation == 'snapshot':
            paths = [p for p, entry in self.variables.items() if entry['rank'] < 0]
            values = self._request(operation, False, lambda client: self._read(
                [(self.variables[p]['id'], ua.AttributeIds.Value) for p in paths]))
            return {'variables': {p: plain(v) for p, v in zip(paths, values)}, 'time': now(), 'source': 'device'}
        if kind == 'get':
            entry = self.variables[path]
            data = self._request(operation, False, lambda client: client.get_node(entry['id']).read_data_value(
                raise_on_bad_status=False))
            status = data.StatusCode
            if status is not None and status.is_bad():
                raise RuntimeError(f'{operation}: the server reported {status.name}')
            result = self._reading(data.Value.Value if data.Value is not None else None, entry, cancelled, operation)
            if status is not None and status.is_uncertain():
                result['status'] = status.name
            result['time'] = data.SourceTimestamp.isoformat() if data.SourceTimestamp else now()
            result['source'] = 'device'
            return result
        if kind == 'set':
            entry = self.variables[path]
            value = ua.DataValue(ua.Variant(arguments['value'], entry['variant']))
            self._request(operation, True, lambda client: client.get_node(entry['id']).write_value(value))
            return {'value': arguments['value'], **({'unit': entry['unit']} if entry['unit'] else {}),
                    'time': now(), 'source': 'driver'}
        from asyncua.sync import call_method_full
        entry = self.methods[path]
        values = [ua.Variant(arguments[name], variant) for name, variant, _ in entry['inputs']]
        reply = self._request(operation, True, lambda client: call_method_full(
            client.get_node(entry['parent']), entry['id'], *values))
        outputs = reply.OutputArguments or []
        named = entry['outputs'] if len(entry['outputs']) == len(outputs) and all(entry['outputs']) else None
        return {'outputs': {n: plain(v) for n, v in zip(named, outputs)} if named else plain(outputs),
                'time': now(), 'source': 'device'}

    def _reading(self, value, entry, context, operation):
        name = entry['variant'].name
        dtype = INTEGERS[name][0] if name in INTEGERS else FLOATS.get(name)
        unit = {'unit': entry['unit']} if entry['unit'] else {}
        regular = flatten(value) if isinstance(value, list) and dtype else None
        if regular:
            values, shape = regular
            payload = struct.pack('<' + str(len(values)) + PACK[dtype], *values)
            spec = arrays.check_result({'dtype': dtype, 'byte_order': 'little', 'shape': shape, **unit})
            return arrays.result(payload, spec, context, operation.replace('.', '-'))
        return {'value': plain(value), **unit}
