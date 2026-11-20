"""Small measurement reductions for experiment programs, using the standard library.

An Estimate carries signed contributions from named independent error sources.
The same name in two estimates denotes the *same* error: covariance is retained
when a dark reading, reference or calibration is reused. Contributions and the
reported combined uncertainty are standard uncertainties, not confidence bounds.

Nonlinear propagation is a first-order approximation. Repeated observations in
RunningMean must be independent measurements of a stationary quantity; drift and
correlated noise require an appropriate model in the research program.
"""
from dataclasses import dataclass, field
import math
from collections.abc import Mapping
from types import MappingProxyType


def _finite(value, name):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(name + ' must be a finite number')
    return float(value)


def _components(value):
    if not isinstance(value, Mapping):
        raise ValueError('components maps source names to signed standard-uncertainty contributions')
    result = {}
    for name, contribution in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Each uncertainty source needs a nonempty name')
        contribution = _finite(contribution, 'Component ' + name)
        if contribution:
            result[name] = contribution
    return result


@dataclass(frozen=True)
class Estimate:
    """A scalar estimate and its uncertainty contributions, all in ``unit``.

    Units are explicit labels. Addition/subtraction requires identical labels;
    use ``scaled(factor, unit=...)`` for an explicit conversion. No unit conversion
    or correlation is inferred from an instrument or a calibration certificate.
    """
    value: float
    unit: str = ''
    components: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'value', _finite(self.value, 'value'))
        if not isinstance(self.unit, str):
            raise ValueError('unit must be text; use an empty string for dimensionless values')
        object.__setattr__(self, 'components', MappingProxyType(_components(self.components)))
        _finite(self.standard_uncertainty, 'combined standard uncertainty')

    @classmethod
    def measured(cls, value, standard_uncertainty, *, source, unit=''):
        """One named error source. Divide an expanded uncertainty by its coverage factor first."""
        uncertainty = _finite(standard_uncertainty, 'standard_uncertainty')
        if uncertainty < 0:
            raise ValueError('standard_uncertainty must be nonnegative')
        return cls(value, unit, {source: uncertainty})

    @property
    def standard_uncertainty(self):
        return math.hypot(*self.components.values())

    def covariance(self, other):
        """Covariance in the product of the two units, from their shared sources."""
        if not isinstance(other, Estimate):
            raise TypeError('covariance takes another Estimate')
        return math.fsum(value * other.components.get(name, 0.0) for name, value in self.components.items())

    def scaled(self, factor, *, unit=None):
        """Multiply value and signed contributions by an exact factor; optionally change the unit label."""
        factor = _finite(factor, 'factor')
        return propagate(self.value * factor, [(self, factor)], unit=self.unit if unit is None else unit)

    def __mul__(self, factor):
        return self.scaled(factor)

    __rmul__ = __mul__

    def __truediv__(self, divisor):
        if isinstance(divisor, Estimate):
            _same_unit(self, divisor)
            return normalize(self, divisor)
        divisor = _finite(divisor, 'divisor')
        if not divisor:
            raise ValueError('divisor must be nonzero')
        return self.scaled(1.0 / divisor)

    def __neg__(self):
        return self.scaled(-1.0)

    def __add__(self, other):
        _same_unit(self, other)
        return propagate(self.value + other.value, [(self, 1.0), (other, 1.0)], unit=self.unit)

    def __sub__(self, other):
        _same_unit(self, other)
        return propagate(self.value - other.value, [(self, 1.0), (other, -1.0)], unit=self.unit)

    def to_dict(self):
        """JSON-ready result; components retain correlations when loaded in another program."""
        return {'value': self.value, 'unit': self.unit, 'standard_uncertainty': self.standard_uncertainty,
                'components': dict(self.components)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - {'value', 'unit', 'components', 'standard_uncertainty'}:
            raise ValueError('An estimate contains value, unit, components and optional standard_uncertainty')
        if not {'value', 'unit', 'components'} <= value.keys():
            raise ValueError('An estimate needs value, unit and components')
        estimate = cls(value['value'], value['unit'], value['components'])
        if 'standard_uncertainty' in value:
            uncertainty = _finite(value['standard_uncertainty'], 'standard_uncertainty')
            if uncertainty < 0 or not math.isclose(uncertainty, estimate.standard_uncertainty, rel_tol=1e-12):
                raise ValueError('standard_uncertainty does not match the saved components')
        return estimate


def _same_unit(first, second):
    if not isinstance(second, Estimate):
        raise TypeError('Use an Estimate for each quantity, including exact constants')
    if first.unit != second.unit:
        raise ValueError(f'Units differ: {first.unit!r} and {second.unit!r}; convert explicitly with scaled')


def propagate(value, terms, *, unit=''):
    """Propagate a differentiable function at its input estimates.

    ``terms`` contains (estimate, partial derivative) pairs. The derivatives
    convert each input's unit to the output unit. Shared contributions are added
    with their signs *before* forming the combined standard uncertainty.
    """
    components = {}
    for estimate, derivative in terms:
        if not isinstance(estimate, Estimate):
            raise TypeError('Each propagation term starts with an Estimate')
        derivative = _finite(derivative, 'derivative')
        for name, contribution in estimate.components.items():
            components.setdefault(name, []).append(contribution * derivative)
    return Estimate(value, unit, {name: math.fsum(parts) for name, parts in components.items()})


def normalize(signal, reference, dark=None):
    """Return (signal - dark) / (reference - dark), with a shared dark if given.

    All quantities use the same unit. The result is dimensionless. A zero
    corrected reference is refused. If its uncertainty is appreciable relative
    to its value, a first-order estimate may not describe the ratio distribution;
    use a suitable nonlinear model instead. This function does not select an
    instrument-dependent threshold.
    """
    _same_unit(signal, reference)
    if dark is not None:
        _same_unit(signal, dark)
        signal, reference = signal - dark, reference - dark
    if not reference.value:
        raise ValueError('The corrected reference is zero; the normalized measurement is undefined')
    value = signal.value / reference.value
    return propagate(value, [(signal, 1.0 / reference.value), (reference, -value / reference.value)])


class RunningMean:
    """Constant-memory mean and sample variance, with mergeable JSON progress.

    This estimates the repeatability contribution from independent observations.
    Calibration/shared contributions supplied to ``estimate`` are already those
    of the mean; they are not divided by the number of readings.
    """
    def __init__(self):
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0

    def add(self, value):
        value = _finite(value, 'observation')
        count = self.count + 1
        delta = value - self.mean
        mean = self.mean + delta / count
        m2 = self.m2 + delta * (value - mean)
        _finite(mean, 'mean')
        _finite(m2, 'sum of squared deviations')
        self.count, self.mean, self.m2 = count, mean, max(0.0, m2)
        return self

    def merge(self, other):
        """Combine disjoint observation groups without retaining their individual values."""
        if not isinstance(other, RunningMean):
            raise TypeError('merge takes another RunningMean')
        if other is self:
            raise ValueError('merge needs disjoint observation groups, not the same group twice')
        if not other.count:
            return self
        if not self.count:
            self.count, self.mean, self.m2 = other.count, other.mean, other.m2
            return self
        count = self.count + other.count
        delta = other.mean - self.mean
        mean = self.mean + delta * (other.count / count)
        m2 = self.m2 + other.m2 + delta * delta * (self.count / count) * other.count
        _finite(mean, 'mean')
        _finite(m2, 'sum of squared deviations')
        self.count, self.mean, self.m2 = count, mean, max(0.0, m2)
        return self

    @property
    def standard_error(self):
        """Sample standard deviation divided by sqrt(count); unknown before two observations."""
        return math.sqrt(self.m2 / (self.count - 1) / self.count) if self.count > 1 else None

    def estimate(self, *, source, unit='', components=None):
        """Name this observation group's repeatability, and add separately supplied shared components.

        Reuse ``source`` for the same estimate. Overlapping groups have covariance
        this constant-memory summary does not retain; do not treat their means as
        independent, or name their different repeatability terms as one error.
        """
        if self.count < 2:
            raise ValueError('At least two observations are needed to estimate repeatability')
        parts = _components({} if components is None else components)
        if source in parts:
            raise ValueError('The repeatability source must differ from the shared uncertainty sources')
        parts[source] = self.standard_error
        return Estimate(self.mean, unit, parts)

    def to_dict(self):
        return {'count': self.count, 'mean': self.mean, 'm2': self.m2}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {'count', 'mean', 'm2'}:
            raise ValueError('RunningMean progress contains count, mean and m2')
        count = value['count']
        if type(count) is not int or count < 0:
            raise ValueError('count must be a nonnegative integer')
        mean, m2 = _finite(value['mean'], 'mean'), _finite(value['m2'], 'm2')
        if m2 < 0 or count < 2 and m2 != 0 or count == 0 and mean != 0:
            raise ValueError('RunningMean progress is inconsistent with its observation count')
        result = cls()
        result.count, result.mean, result.m2 = count, mean, m2
        return result
