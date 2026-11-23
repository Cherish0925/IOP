"""Units of physical quantities: an argument given with its unit is converted to the unit an operation declares.

A unit is a product of symbols with integer powers, each with an optional SI prefix: ``mV``, ``m/s^2``,
``W m^-2``, ``J/(mol K)``, ``µm``. Dimensions are those of the SI base quantities, with plane angle kept
apart so that radians per second are not taken for hertz. Celsius and Fahrenheit temperatures are
converted with their offset when they stand alone, and as temperature differences inside compound units.
Logarithmic units such as dB and dBm, and units this table does not know, are compared by name only.
"""
from __future__ import annotations
import math
import re

# Dimension exponents: length, mass, time, current, temperature, amount, luminous intensity, plane angle.
BASE = ('m', 'kg', 's', 'A', 'K', 'mol', 'cd', 'rad')


def _d(**powers):
    return tuple(powers.get(name, 0) for name in ('L', 'M', 'T', 'I', 'Th', 'N', 'J', 'An'))


NONE = _d()
# symbol: (factor to SI, dimension, offset, takes a prefix)
UNITS = {
    'm': (1, _d(L=1), 0, True), 'g': (1e-3, _d(M=1), 0, True), 's': (1, _d(T=1), 0, True),
    'A': (1, _d(I=1), 0, True), 'K': (1, _d(Th=1), 0, True), 'mol': (1, _d(N=1), 0, True),
    'cd': (1, _d(J=1), 0, True), 'rad': (1, _d(An=1), 0, True),
    'Hz': (1, _d(T=-1), 0, True), 'N': (1, _d(M=1, L=1, T=-2), 0, True), 'Pa': (1, _d(M=1, L=-1, T=-2), 0, True),
    'J': (1, _d(M=1, L=2, T=-2), 0, True), 'W': (1, _d(M=1, L=2, T=-3), 0, True), 'C': (1, _d(T=1, I=1), 0, True),
    'V': (1, _d(M=1, L=2, T=-3, I=-1), 0, True), 'F': (1, _d(M=-1, L=-2, T=4, I=2), 0, True),
    'Ω': (1, _d(M=1, L=2, T=-3, I=-2), 0, True), 'ohm': (1, _d(M=1, L=2, T=-3, I=-2), 0, True),
    'S': (1, _d(M=-1, L=-2, T=3, I=2), 0, True), 'Wb': (1, _d(M=1, L=2, T=-2, I=-1), 0, True),
    'T': (1, _d(M=1, T=-2, I=-1), 0, True), 'H': (1, _d(M=1, L=2, T=-2, I=-2), 0, True),
    'L': (1e-3, _d(L=3), 0, True), 'l': (1e-3, _d(L=3), 0, True), 'eV': (1.602176634e-19, _d(M=1, L=2, T=-2), 0, True),
    'bar': (1e5, _d(M=1, L=-1, T=-2), 0, True), 'Torr': (101325 / 760, _d(M=1, L=-1, T=-2), 0, True),
    'atm': (101325, _d(M=1, L=-1, T=-2), 0, False), 'psi': (6894.757293168, _d(M=1, L=-1, T=-2), 0, False),
    'G': (1e-4, _d(M=1, T=-2, I=-1), 0, False), 'gauss': (1e-4, _d(M=1, T=-2, I=-1), 0, False),
    'min': (60, _d(T=1), 0, False), 'h': (3600, _d(T=1), 0, False), 'day': (86400, _d(T=1), 0, False),
    'deg': (math.pi / 180, _d(An=1), 0, False), '°': (math.pi / 180, _d(An=1), 0, False),
    'sr': (1, _d(An=2), 0, False),
    '°C': (1, _d(Th=1), 273.15, False), 'degC': (1, _d(Th=1), 273.15, False),
    '°F': (5 / 9, _d(Th=1), 459.67 * 5 / 9, False), 'degF': (5 / 9, _d(Th=1), 459.67 * 5 / 9, False),
    '%': (1e-2, NONE, 0, False), 'ppm': (1e-6, NONE, 0, False), 'ppb': (1e-9, NONE, 0, False),
}
PREFIXES = {'Y': 1e24, 'Z': 1e21, 'E': 1e18, 'P': 1e15, 'T': 1e12, 'G': 1e9, 'M': 1e6, 'k': 1e3, 'h': 1e2,
            'da': 1e1, 'd': 1e-1, 'c': 1e-2, 'm': 1e-3, 'u': 1e-6, 'µ': 1e-6, 'μ': 1e-6, 'n': 1e-9, 'p': 1e-12,
            'f': 1e-15, 'a': 1e-18, 'z': 1e-21, 'y': 1e-24}
SUPERSCRIPTS = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁻', '0123456789-')
FACTOR = re.compile(r'([^\s^*·0-9⁰¹²³⁴⁵⁶⁷⁸⁹⁻+-]+)(?:\^|\*\*)?([-+]?[0-9]+|[⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)?$')
QUANTITY = re.compile(r'\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$')


class Unit:
    """A unit as a factor to SI, a dimension and, for a lone Celsius or Fahrenheit temperature, an offset."""
    def __init__(self, factor=1.0, dimension=NONE, offset=0.0):
        self.factor, self.dimension, self.offset = factor, dimension, offset

    def __mul__(self, other):
        return Unit(self.factor * other.factor, tuple(a + b for a, b in zip(self.dimension, other.dimension)))

    def __pow__(self, power):
        return Unit(self.factor ** power, tuple(a * power for a in self.dimension))


def _symbol(text):
    if text in UNITS:
        factor, dimension, offset, _ = UNITS[text]
        return Unit(factor, dimension, offset)
    for size in (2, 1):
        prefix, rest = text[:size], text[size:]
        if prefix in PREFIXES and rest in UNITS and UNITS[rest][3]:
            factor, dimension, _, _ = UNITS[rest]
            return Unit(PREFIXES[prefix] * factor, dimension)
    raise ValueError(f'unknown unit {text}')


def _split(text, separators):
    """Split at separators outside parentheses."""
    parts, depth, current = [], 0, ''
    for char in text:
        depth += (char == '(') - (char == ')')
        if depth < 0:
            raise ValueError('unbalanced parentheses')
        if depth == 0 and char in separators:
            parts.append(current)
            current = ''
        else:
            current += char
    if depth:
        raise ValueError('unbalanced parentheses')
    return parts + [current]


def _product(text):
    unit, lone = Unit(), None
    factors = [f for f in _split(text.strip(), ' *·') if f]
    for item in factors:
        if item.startswith('(') and item.endswith(')'):
            unit = unit * parse(item[1:-1])
            continue
        match = FACTOR.match(item)
        if not match:
            raise ValueError(f'cannot read {item}')
        symbol, power = match.group(1), int((match.group(2) or '1').translate(SUPERSCRIPTS))
        found = _symbol(symbol)
        lone = found if len(factors) == 1 and power == 1 else None
        unit = unit * found ** power
    if lone is not None and lone.offset:
        unit.offset = lone.offset
    return unit


def parse(text):
    """The unit a text names; raises ValueError for one this table does not know."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('a unit is a nonempty text')
    text = text.strip()
    if text in ('1', '-'):
        return Unit()
    parts = _split(text, '/')
    unit = _product(parts[0]) if parts[0].strip() not in ('', '1') else Unit()
    offset = unit.offset if len(parts) == 1 else 0.0
    for part in parts[1:]:
        unit = unit * _product(part) ** -1
    unit.offset = offset
    return unit


def convert(value, source, target):
    """The number ``value`` in unit ``source`` is in unit ``target``; raises ValueError when they differ in dimension."""
    if source == target:
        return value
    try:
        given, wanted = parse(source), parse(target)
    except ValueError as error:
        raise ValueError(f'{source} cannot be converted to {target}: {error}; give the value in {target}') from None
    if given.dimension != wanted.dimension:
        raise ValueError(f'{source} is not a unit of the same quantity as {target}')
    return ((value * given.factor + given.offset) - wanted.offset) / wanted.factor


def quantity(value):
    """A number with its unit, given as {"value": 5, "unit": "mV"} or "5 mV"; None when ``value`` is neither."""
    if isinstance(value, dict) and value.keys() == {'value', 'unit'}:
        number, unit = value['value'], value['unit']
    elif isinstance(value, str):
        match = QUANTITY.match(value)
        if not match or not match.group(2):
            return None
        number, unit = float(match.group(1)), match.group(2)
    else:
        return None
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) \
            or not isinstance(unit, str) or not unit.strip():
        raise ValueError('a quantity is {"value": a finite number, "unit": its unit}')
    return number, unit.strip()
