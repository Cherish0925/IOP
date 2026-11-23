"""Published condition schemas accept the same unit-bearing tolerances as the runtime."""
import importlib.util
import json
import unittest
from pathlib import Path

from ai4instruments.iop import conditions


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'jsonschema is an optional schema consumer')
class ConditionSchemaCase(unittest.TestCase):
    def test_quantity_objects_are_accepted_in_each_description(self):
        from jsonschema import Draft202012Validator
        root = Path(__file__).resolve().parents[2] / 'iop/schema'
        cases = [
            {'target': {'value': 25, 'unit': '°C'}, 'within': {'value': 20, 'unit': 'mK'}},
            {'minimum': {'value': 273.15, 'unit': 'K'}, 'maximum': {'value': 30, 'unit': '°C'}},
            {'target': 300, 'relative': {'value': 0.1, 'unit': '%'}},
        ]
        for name, definition in (('platform', 'tolerance'), ('device', 'instrumentTolerance'),
                                 ('instrument-profile', 'instrumentTolerance')):
            document = json.loads((root / (name + '.schema.json')).read_text())
            Draft202012Validator.check_schema(document)
            validator = Draft202012Validator({'$defs': document['$defs'], '$ref': '#/$defs/' + definition})
            for tolerance in cases:
                with self.subTest(schema=name, tolerance=tolerance):
                    validator.validate(tolerance)
                    checked = conditions.check([{'id': 'temperature', 'description': 'Temperature in range',
                        'verify': {'device': 'thermometer', 'operation': 'read'}, 'accept': tolerance}],
                        unit_of=lambda step: 'K')[0]
                    self.assertTrue(all(not isinstance(checked['accept'][key], dict)
                        for key in tolerance))
            for malformed in ({'value': 25}, {'value': True, 'unit': 'K'}, {'value': 25, 'unit': 'K', 'units': 'K'}):
                self.assertFalse(validator.is_valid({'target': malformed, 'within': 0.02}))
