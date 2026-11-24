"""Quantities with units: converted to the unit an operation declares, refused when they measure something else."""
import unittest
from ai4instruments.iop import IOPError
from ai4instruments.iop.drivers import parameters
from ai4instruments.iop.units import convert, difference, parse, quantity
from . import test_service as fixture


class UnitsCase(unittest.TestCase):
    def test_conversions_between_units_of_one_quantity(self):
        for value, source, target, expected in (
                (5, 'mV', 'V', 0.005), (1, 'kHz', 'Hz', 1e3), (1, 'GHz', 'MHz', 1e3), (1, 'µm', 'nm', 1e3), (1, 'μm', 'm', 1e-6),
                (25, '°C', 'K', 298.15), (300, 'K', '°C', 26.85), (32, '°F', '°C', 0), (4.2, 'K', 'mK', 4200),
                (25, '(°C)', 'K', 298.15), (1, '(°C)/s', 'K/s', 1),
                (1, 'K/min', 'mK/s', 1000 / 60), (1, 'mbar', 'Pa', 100), (760, 'Torr', 'Pa', 101325), (1, 'hPa', 'mbar', 1),
                (1, 'atm', 'kPa', 101.325), (90, 'deg', 'rad', 1.5707963267948966), (10, 'G', 'mT', 1), (1, 'h', 'min', 60),
                (1, 'W m^-2', 'mW/cm^2', 0.1), (2, 'm/s^2', 'm s⁻²', 2), (2, 'm/s**2', 'm s⁻²', 2),
                (1, 'J/(mol K)', 'J mol^-1 K^-1', 1),
                (1, 'L', 'cm³', 1000), (1, 'kΩ', 'ohm', 1000), (1, 'mmol/L', 'mol/m^3', 1), (1, 'eV', 'J', 1.602176634e-19),
                (1, '%', 'ppm', 1e4), (1, 'A/s', 'mA/ms', 1)):
            self.assertAlmostEqual(convert(value, source, target), expected, delta=1e-9 * max(1, abs(expected)),
                                   msg=f'{value} {source} in {target}')

    def test_units_of_different_quantities_are_refused(self):
        for source, target, message in (('V', 'A', 'not a unit of the same quantity'),
                                        ('rad/s', 'Hz', 'not a unit of the same quantity'),
                                        ('C', 'K', 'not a unit of the same quantity'),
                                        ('dBm', 'mW', 'unknown unit dBm'), ('m', 'furlong', 'unknown unit furlong')):
            with self.assertRaisesRegex(ValueError, message):
                convert(1, source, target)
        self.assertEqual(convert(3, 'counts', 'counts'), 3)
        with self.assertRaisesRegex(ValueError, 'unbalanced parentheses'):
            parse('J/(mol K')

    def test_a_quantity_is_a_number_with_its_unit(self):
        self.assertEqual(quantity('250 mV'), (250.0, 'mV'))
        self.assertEqual(quantity({'value': 2, 'unit': 'kHz'}), (2, 'kHz'))
        self.assertEqual(quantity('-3.2e-3 A'), (-0.0032, 'A'))
        self.assertIsNone(quantity('CH1'))
        self.assertIsNone(quantity(5))
        with self.assertRaisesRegex(ValueError, 'a quantity is'):
            quantity({'value': True, 'unit': 'V'})

    def test_temperature_differences_preserve_small_tolerances(self):
        for value in (1.0, 1e-9, 1e-14):
            self.assertAlmostEqual(difference(value, '°C', 'K') / value, 1)
            self.assertAlmostEqual(difference(value, '°F', 'K') / value, 5 / 9)
            self.assertAlmostEqual(difference(value, 'K', '°F') / value, 9 / 5)
        self.assertEqual(difference(0.01, 'dBm', 'dBm'), 0.01)
        with self.assertRaisesRegex(ValueError, 'not a unit of the same quantity'):
            difference(1, 'V', 'K')

    def test_conversions_do_not_lose_values_in_an_intermediate_si_unit(self):
        self.assertEqual(convert(1e-14, '°C', 'degC'), 1e-14)
        self.assertAlmostEqual(convert(1e308, 'kV', 'MV') / 1e305, 1)

    def test_arguments_are_converted_before_their_bounds_apply(self):
        spec = {'parameters': {'volts': {'type': 'number', 'unit': 'V', 'minimum': -10, 'maximum': 10},
                               'rate': {'type': 'integer', 'unit': 'Hz'}, 'gain': {'type': 'number', 'default': 1}}}
        self.assertEqual(parameters(spec, {'volts': '250 mV', 'rate': {'value': 2, 'unit': 'kHz'}}),
                         {'volts': 0.25, 'rate': 2000, 'gain': 1})
        for supplied, message in (({'volts': '5 A', 'rate': 1}, 'volts is in V: A is not a unit of the same quantity as V'),
                                  ({'volts': '20 V', 'rate': 1}, 'volts is 20.0 V, outside -10 to 10 V'),
                                  ({'volts': 1, 'rate': '1.5 Hz'}, 'not an integer'),
                                  ({'volts': 1, 'rate': 1, 'gain': '3 V'}, 'gain declares no unit; give a plain number')):
            with self.assertRaisesRegex(ValueError, message):
                parameters(spec, supplied)

    def test_integer_arguments_do_not_round_fractional_large_values(self):
        spec = {'parameters': {'rate': {'type': 'integer', 'unit': 'Hz'}}}
        for value in ('1000000000.5 Hz', '1000000.0005 kHz'):
            with self.assertRaisesRegex(ValueError, 'not an integer'):
                parameters(spec, {'rate': value})
        self.assertEqual(parameters(spec, {'rate': '1 GHz'}), {'rate': 1000000000})
        with self.assertRaisesRegex(ValueError, 'finite number'):
            parameters(spec, {'rate': '1e300 GHz'})

    def test_array_items_are_converted_and_keep_declared_units(self):
        spec = {'parameters': {
            'bias': {'type': 'array', 'unit': 'V', 'items': {'type': 'number', 'maximum': 1}},
            'pressure': {'type': 'array', 'items': {'type': 'number', 'unit': 'Pa'}},
            'wavelength': {'type': 'array', 'unit': 'nm', 'items': {'type': 'array', 'items': {'type': 'number'}}},
            'temperature': {'type': 'array', 'unit': 'K', 'items': {'type': 'number', 'unit': '°C'}}}}
        supplied = {'bias': ['250 mV', {'value': 1, 'unit': 'V'}], 'pressure': ['1 mbar'],
                    'wavelength': [['1 µm']], 'temperature': ['273.15 K']}
        result = parameters(spec, supplied)
        self.assertEqual({k: v for k, v in result.items() if k != 'wavelength'},
                         {'bias': [0.25, 1], 'pressure': [100], 'temperature': [0]})
        self.assertAlmostEqual(result['wavelength'][0][0], 1000)
        self.assertEqual(supplied['bias'][0], '250 mV')
        with self.assertRaisesRegex(ValueError, 'above 1 V'):
            parameters(spec, {**supplied, 'bias': ['2000 mV']})
        with self.assertRaisesRegex(ValueError, 'not a unit of the same quantity'):
            parameters(spec, {**supplied, 'pressure': ['1 mV']})


class SubmittedUnitsCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_quantity_objects_and_mixed_range_bounds_reach_the_instrument(self):
        for sweep in ({'angle_deg': [{'value': 0, 'unit': 'rad'}, {'value': 0.1, 'unit': 'rad'}]},
                      {'angle_deg': {'start': {'value': 0, 'unit': 'rad'}, 'stop': '0.1 rad', 'points': 2}}):
            with self.subTest(sweep=sweep):
                run = self.client.submit([{'id': 'scan', 'device': 'stage', 'operation': 'move_to',
                                           'sweep': sweep, 'arguments': {'duration': 0}}])
                run.result(5)
                angles = [r['arguments']['angle_deg'] for r in run.info()['operation_records']]
                self.assertEqual([round(a, 6) for a in angles], [0, 5.729578])
        self.client.register({'id': 'limited', 'driver': 'simulated', 'config': {'kind': 'stage'},
                              'limits': {'move_to': {'angle_deg': {'maximum': 45, 'note': 'Mount stop'}}}})
        with self.assertRaisesRegex(IOPError, 'Mount stop'):
            self.client.submit([{'id': 'scan', 'device': 'limited', 'operation': 'move_to',
                                 'sweep': {'angle_deg': [{'value': 1, 'unit': 'rad'}]}}])

    def test_platform_procedures_keep_unit_bearing_ranges(self):
        steps = [{'id': 'scan', 'device': 'stage', 'operation': 'move_to',
                  'sweep': {'angle_deg': {'center': {'value': 0, 'unit': 'rad'}, 'span': '0.2 rad', 'points': 3}},
                  'arguments': {'duration': 0}}]
        saved = self.client.configure_platform({'schema_version': 1, 'id': 'bench', 'devices': ['stage'],
            'procedures': {'initialize': {'steps': steps}}})
        self.assertEqual(saved['definition']['procedures']['initialize']['steps'], steps)
        run = self.client.procedure('bench')
        run.result(5)
        angles = [r['arguments']['angle_deg'] for r in run.info()['operation_records']]
        self.assertEqual([round(a, 6) for a in angles], [-5.729578, 0, 5.729578])

    def test_an_experiment_records_the_converted_argument_and_refuses_another_quantity(self):
        run = self.client.submit([{'id': 'turn', 'device': 'stage', 'operation': 'move_to',
                                   'arguments': {'angle_deg': '0.5 rad', 'duration': '20 ms'}}])
        self.assertAlmostEqual(run.result(5)['turn']['angle_actual'], 28.64788975654116)
        recorded = run.info()['operation_records'][0]['arguments']
        self.assertAlmostEqual(recorded['duration'], 0.02)
        with self.assertRaisesRegex(IOPError, r'Step turn \(stage.move_to\): angle_deg is in deg: '
                                              'mm is not a unit of the same quantity as deg'):
            self.client.submit([{'id': 'turn', 'device': 'stage', 'operation': 'move_to', 'arguments': {'angle_deg': '5 mm'}}])
        # A sweep's range is given in any unit of the parameter; its positions are in the declared one.
        run = self.client.submit([{'id': 'scan', 'device': 'stage', 'operation': 'move_to',
                                   'sweep': {'angle_deg': {'start': '0 rad', 'stop': '0.1 rad', 'points': 3}},
                                   'arguments': {'duration': '1 ms'}}])
        run.result(5)
        angles = [r['arguments']['angle_deg'] for r in run.info()['operation_records']]
        self.assertEqual([round(a, 6) for a in angles], [0.0, 2.864789, 5.729578])
        with self.assertRaisesRegex(IOPError, 'angle_deg is in deg: V is not a unit of the same quantity as deg'):
            self.client.submit([{'id': 'scan', 'device': 'stage', 'operation': 'move_to',
                                 'sweep': {'angle_deg': ['1 V', '2 V']}}])
        # A limit the setup imposes applies to the converted number.
        self.client.register({'id': 'limited', 'driver': 'simulated', 'config': {'kind': 'stage'},
                              'limits': {'move_to': {'angle_deg': {'maximum': 45, 'note': 'The mount hits its stop'}}}})
        with self.assertRaisesRegex(IOPError, 'The mount hits its stop'):
            self.client.submit([{'id': 'turn', 'device': 'limited', 'operation': 'move_to', 'arguments': {'angle_deg': '1 rad'}}])


class RangeUnitsCase(unittest.TestCase):
    def test_integer_ranges_reuse_argument_conversion_without_rounding_fractional_values(self):
        from ai4instruments.iop import library
        spec = {'parameters': {'frequency': {'type': 'integer', 'unit': 'Hz'}}}
        step = {'id': 'scan', 'device': 'clock', 'operation': 'set',
                'sweep': {'frequency': {'start': '1 kHz', 'stop': {'value': 2, 'unit': 'kHz'}, 'points': 3}}}
        flat, _, _ = library.expand([step], lambda name: None, lambda d, o: spec)
        values = [s['arguments']['frequency'] for s in flat]
        self.assertEqual(values, [1000, 1500, 2000])
        self.assertTrue(all(type(v) is int for v in values))
        step['sweep']['frequency']['stop'] = '1000000000.5 Hz'
        with self.assertRaisesRegex(ValueError, 'not an integer'):
            library.expand([step], lambda name: None, lambda d, o: spec)

    def test_quantity_objects_in_saved_methods_are_converted_at_expansion(self):
        from ai4instruments.iop import library
        method = {'id': 'ramp', 'description': 'Sweep a supplied voltage range', 'layer': 'standard',
                  'parameters': {'stop': {'type': 'number', 'unit': 'V'}},
                  'steps': [{'id': 'scan', 'device': 'supply', 'operation': 'set',
                             'sweep': {'voltage': {'start': {'value': 250, 'unit': 'mV'},
                                                   'stop': '${stop}', 'points': 2}}}]}
        library.check(method, lambda name: None)
        steps, _, _ = library.expand([{'id': 'r', 'operation': 'ramp', 'arguments': {'stop': '500 mV'}}],
                                      lambda name: dict(method, revision=1), lambda d, o: {'parameters': {
                                          'voltage': {'type': 'number', 'unit': 'V'}}})
        self.assertEqual([s['arguments']['voltage'] for s in steps], [0.25, 0.5])

    def test_categorical_labels_and_result_paths_are_not_quantities(self):
        from ai4instruments.iop import library
        step = {'id': 'scan', 'device': 'meter', 'operation': 'mode', 'sweep': {'mode': ['1 V', '2 V']}}
        flat, _, _ = library.expand([step], lambda name: None,
                                    lambda d, o: {'parameters': {'mode': {'type': 'string'}}})
        self.assertEqual([s['arguments']['mode'] for s in flat], ['1 V', '2 V'])
        step = {'id': 'scan', 'device': 'meter', 'operation': 'read', 'sweep': {'voltage': {
            'start': 0, 'stop': 1, 'points': 2, 'adapt': ['1 V']}}}
        self.assertIsNotNone(library.swept(step))
        for malformed in ('search', [], 4):
            with self.assertRaises(ValueError):
                library.expand([{'id': 'scan', 'device': 'meter', 'operation': 'read', 'seek': malformed}],
                               lambda name: None)

    def test_seek_quantity_objects_preserve_temperature_differences(self):
        from ai4instruments.iop import library
        over = {'minimum': {'value': 273.15, 'unit': 'K'}, 'maximum': {'value': 283.15, 'unit': 'K'},
                'step': {'value': 1, 'unit': 'K'}, 'resolution': {'value': 100, 'unit': 'mK'}}
        flat, _, _ = library.expand([{'id': 'find', 'device': 'oven', 'operation': 'set',
            'seek': {'maximize': ['value'], 'over': {'temperature': over}, 'points': 5}}],
            lambda name: None, lambda d, o: {'parameters': {'temperature': {'type': 'number', 'unit': '°C'}}})
        converted = flat[-1]['gathers']['find']['seek']['over']['temperature']
        for key, expected in {'minimum': 0, 'maximum': 10, 'step': 1, 'resolution': 0.1}.items():
            self.assertAlmostEqual(converted[key], expected)

    def test_seek_ranges_convert_positions_and_widths(self):
        from ai4instruments.iop.library import in_units
        step = {'id': 'find', 'device': 'oven', 'operation': 'set',
                'seek': {'maximize': ['value'], 'over': {'celsius': {'minimum': '300 K', 'maximum': '350 K', 'step': '5 K',
                                                                      'resolution': '100 mK', 'start': '320 K'}}}}
        over = in_units(step, {'celsius': {'type': 'number', 'unit': '°C'}})['seek']['over']['celsius']
        expected = {'minimum': 26.85, 'maximum': 76.85, 'step': 5.0, 'resolution': 0.1, 'start': 46.85}
        for key, value in expected.items():
            self.assertAlmostEqual(over[key], value, msg=key)


if __name__ == '__main__':
    unittest.main()
