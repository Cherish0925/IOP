"""Measurement uncertainty retains shared errors; continuation never repeats an accepted reading."""
import importlib.util
import json
import math
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from ai4instruments.iop import Driver, IOPError
from ai4instruments.iop.drivers import UnconfirmedChange
from ai4instruments.iop.measurements import Estimate, RunningMean, normalize, propagate
from . import test_service as fixture


EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'iop' / 'reference-measurement'
spec = importlib.util.spec_from_file_location('reference_measurement_example', EXAMPLE / 'experiment.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class MeasurementCase(unittest.TestCase):
    def test_shared_sources_cancel_before_combining_uncertainty(self):
        signal = Estimate(6, 'W', {'signal': 0.2, 'gain': 0.06})
        reference = Estimate(11, 'W', {'reference': 0.3, 'gain': 0.11})
        dark = Estimate(1, 'W', {'dark': 0.1, 'gain': 0.01})
        result = normalize(signal, reference, dark)
        self.assertEqual(result.value, 0.5)
        self.assertEqual(result.unit, '')
        # d/d(signal)=1/10, d/d(reference)=-5/100, d/d(dark)=(6-11)/100.
        expected = math.sqrt((0.2 / 10)**2 + (0.3 * 5 / 100)**2 + (0.1 * 5 / 100)**2)
        self.assertAlmostEqual(result.standard_uncertainty, expected, places=15)
        self.assertAlmostEqual(result.components.get('gain', 0), 0, places=16)
        self.assertAlmostEqual(result.components['dark'], -0.005)
        self.assertEqual((signal - signal).standard_uncertainty, 0)
        self.assertEqual((signal / signal).standard_uncertainty, 0)

    def test_averaging_does_not_reduce_shared_calibration(self):
        first = Estimate(10, 'mV', {'read-1': 2, 'calibration': 3})
        second = Estimate(12, 'mV', {'read-2': 2, 'calibration': 3})
        mean = (first + second) / 2
        self.assertEqual(mean.value, 11)
        self.assertEqual(mean.components['calibration'], 3)
        self.assertAlmostEqual(mean.standard_uncertainty, math.sqrt(11))
        self.assertEqual(first.covariance(second), 9)
        self.assertEqual(first.scaled(0.001, unit='V').components['calibration'], 0.003)
        with self.assertRaisesRegex(ValueError, 'Units differ'):
            _ = first + first.scaled(0.001, unit='V')

    def test_general_first_order_model_and_serialization(self):
        # A = pi r^2: a signed sensitivity with a result unit chosen explicitly.
        radius = Estimate.measured(2, 0.01, source='radius', unit='m')
        area = propagate(math.pi * radius.value**2, [(radius, 2 * math.pi * radius.value)], unit='m^2')
        self.assertAlmostEqual(area.standard_uncertainty, 0.04 * math.pi)
        loaded = Estimate.from_dict(json.loads(json.dumps(area.to_dict(), allow_nan=False)))
        self.assertEqual(loaded.to_dict(), area.to_dict())
        self.assertAlmostEqual(loaded.covariance(area), area.standard_uncertainty**2)
        parts = {'a': 1.0}
        fixed = Estimate(1, components=parts)
        parts['a'] = 2
        self.assertEqual(fixed.standard_uncertainty, 1)
        with self.assertRaises(TypeError):
            fixed.components['a'] = 2
        with self.assertRaisesRegex(ValueError, 'does not match'):
            Estimate.from_dict({**area.to_dict(), 'standard_uncertainty': 99})

    def test_welford_mean_restarts_and_merges_disjoint_blocks(self):
        values = [1e9 + i for i in (-3, -1, 1, 3)]
        first = RunningMean().add(values[0]).add(values[1])
        restored = RunningMean.from_dict(json.loads(json.dumps(first.to_dict())))
        restored.add(values[2]).add(values[3])
        first.merge(RunningMean().add(values[2]).add(values[3]))
        self.assertEqual(first.to_dict(), restored.to_dict())
        self.assertEqual(first.mean, 1e9)
        self.assertAlmostEqual(first.standard_error, math.sqrt(5 / 3))
        result = first.estimate(source='repeatability', unit='Hz', components={'clock': 2})
        self.assertEqual(result.components['clock'], 2)
        self.assertAlmostEqual(result.standard_uncertainty, math.sqrt(5 / 3 + 4))
        with self.assertRaisesRegex(ValueError, 'differ'):
            first.estimate(source='clock', components={'clock': 2})
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            first.merge(first)
        with self.assertRaisesRegex(ValueError, 'two observations'):
            RunningMean().add(1).estimate(source='unknown')

    def test_invalid_data_is_not_silently_removed(self):
        for bad in (math.nan, math.inf, -math.inf, True, '1'):
            mean = RunningMean().add(1)
            with self.assertRaisesRegex(ValueError, 'finite'):
                mean.add(bad)
            self.assertEqual(mean.count, 1)
            with self.assertRaisesRegex(ValueError, 'finite'):
                Estimate(bad)
        for state in ({'count': 1, 'mean': 2, 'm2': 1}, {'count': -1, 'mean': 0, 'm2': 0},
                      {'count': 0, 'mean': 1, 'm2': 0}, {'count': 2, 'mean': 0, 'm2': -1}):
            with self.assertRaises(ValueError):
                RunningMean.from_dict(state)
        with self.assertRaisesRegex(ValueError, 'corrected reference is zero'):
            normalize(Estimate(2, 'W'), Estimate(1, 'W'), Estimate(1, 'W'))


class SequenceDriver(Driver):
    def __init__(self, *args):
        super().__init__(*args)
        self.next = 0
        self.calls = 0

    def describe(self):
        return {'mode': 'simulation', 'operations': {'read': {'effect': 'observe', 'parameters': {}}}}

    def invoke(self, operation, arguments, cancelled):
        self.calls += 1
        if self.calls in self.config.get('unknown_reads', []):
            raise UnconfirmedChange('Simulated instrument did not confirm its outcome')
        if self.calls in self.config.get('failed_reads', []):
            raise ValueError('Simulated read failed without returning a value')
        values = self.config['values']
        value = values[self.next % len(values)]
        self.next += 1
        return {'value': value, 'unit': 'W', 'source': 'simulation'}


class InterruptedClient:
    """Drop a reply after the server saved it, to interrupt at different durability boundaries."""
    def __init__(self, run, method, after):
        self.run, self.method, self.after, self.seen = run, method, after, 0
        self.id = run.id

    def __getattr__(self, name):
        member = getattr(self.run, name)
        if name == 'stream' and self.method == 'stream.append':
            def stream(*args, **kwargs):
                real = member(*args, **kwargs)
                return SimpleNamespace(append=lambda *args, **kwargs: self.intercept(real.append, *args, **kwargs))
            return stream
        if name != self.method:
            return member
        return lambda *args, **kwargs: self.intercept(member, *args, **kwargs)

    def intercept(self, member, *args, **kwargs):
        value = member(*args, **kwargs)
        self.seen += 1
        if self.seen == self.after:
            raise ConnectionError('The client stopped after the server saved the reply')
        return value


class ProgramCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def setup_measurements(self, noisy=False, failed_reads=(), unknown_reads=()):
        parameters = {'min_samples': 4, 'max_samples': 6, 'standard_uncertainty': 0.001}
        for name, value in (('signal', 6), ('reference', 11), ('dark', 1)):
            readings = [value - 0.1, value + 0.1] if noisy else [value]
            config = {'values': readings}
            if name == 'signal':
                config.update(failed_reads=list(failed_reads), unknown_reads=list(unknown_reads))
            self.client.register({'id': name, 'driver': __name__ + ':SequenceDriver', 'config': config})
            parameters[name] = {'device': name, 'operation': 'read', 'path': ['value'], 'unit': 'W',
                                'components': {'common-gain': {'relative': 0.01}}}
        return parameters

    def test_package_runs_with_the_same_saved_data_used_for_precision_stopping(self):
        parameters = self.setup_measurements()
        run = self.client.submit_package(EXAMPLE, resources=['signal', 'reference', 'dark'], parameters=parameters)
        result = run.result(15)
        self.assertEqual(result['samples'], 4)
        self.assertEqual(result['stop'], 'precision')
        self.assertAlmostEqual(result['estimate']['value'], 0.5)
        self.assertEqual(run.info(False)['operation_count'], 12)
        self.assertEqual(run.stream('reference-measurement').info()['chunks'], 4)
        self.assertEqual(len([f for f in run.files() if f['name'].endswith('.json') and '/reference-measurement/' in f['name']]), 4)

    def test_restarting_after_a_lost_read_or_checkpoint_reply_reuses_operations(self):
        parameters = self.setup_measurements(noisy=True)
        for method, count in (('invoke_record', 5), ('checkpoint', 2), ('save_acquisition', 2), ('stream.append', 2)):
            with self.subTest(method=method):
                run = self.client.start_experiment(resources=['signal', 'reference', 'dark'])
                with self.assertRaises(ConnectionError):
                    example.measure(InterruptedClient(run, method, count), parameters)
                result = example.measure(run, parameters)
                self.assertEqual(result['samples'], 6)
                self.assertEqual(result['stop'], 'max_samples')
                self.assertFalse(result['precision_reached'])
                self.assertAlmostEqual(result['estimate']['value'], 0.5)
                self.assertEqual(run.info(False)['operation_count'], 18)
                self.assertEqual(run.stream('reference-measurement').info()['chunks'], 6)
                self.assertEqual(run.checkpoints()['reference-measurement']['state']['completed'], 6)
                run.finish()

    def test_documented_demo_prepares_the_shared_apparatus_before_each_read(self):
        parameters = json.loads((EXAMPLE / 'parameters.demo.json').read_text())
        run = self.client.submit_package(EXAMPLE, resources=['stage', 'meter'], parameters=parameters)
        result = run.result(15)
        self.assertEqual(result['stop'], 'precision')
        self.assertEqual(result['samples'], parameters['min_samples'])
        self.assertAlmostEqual(result['estimate']['value'], 0.5)
        self.assertEqual(run.info(False)['operation_count'], 6 * parameters['min_samples'])

    def test_first_recorded_reading_survives_actual_package_failure_and_resume(self):
        parameters = self.setup_measurements()
        package = Path(self.temp.name) / 'first-round-interruption'
        shutil.copytree(EXAMPLE, package)
        program = package / 'experiment.py'
        source = program.read_text(encoding='utf-8')
        source = 'from pathlib import Path\n' + source
        source = source.replace('            # A reply lost here',
            '            marker = Path(os.environ["IOP_RUN_DIR"]) / "stopped-once"\n'
            '            if not marker.exists():\n'
            '                marker.write_text("first reading completed", encoding="utf-8")\n'
            '                raise RuntimeError("Interrupted after the first completed reading")\n'
            '            # A reply lost here', 1)
        program.write_text(source, encoding='utf-8')
        run = self.client.submit_package(package, resources=['signal', 'reference', 'dark'], parameters=parameters)
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        original = run.operations()['operations']
        self.assertEqual(len(original), 1)
        self.assertEqual(original[0]['state'], 'completed')
        progress = run.checkpoints()['reference-measurement']['state']
        self.assertEqual(progress['completed'], 0)
        self.assertTrue(all(mean['count'] == 0 for mean in progress['means'].values()))
        self.assertEqual(run.resume('continue-first-round')['attempt'], 2)
        result = run.result(15)
        self.assertEqual(result['stop'], 'precision')
        self.assertEqual(result['samples'], 4)
        self.assertAlmostEqual(result['estimate']['value'], 0.5)
        self.assertEqual(run.info(False)['operation_count'], 12)
        self.assertEqual(run.operations()['operations'][0]['id'], original[0]['id'])
        self.assertEqual(run.stream('reference-measurement').info()['chunks'], 4)

    def test_lost_initial_progress_reply_precedes_all_device_and_stream_actions(self):
        parameters = self.setup_measurements()
        with self.client.start_experiment(resources=['signal', 'reference', 'dark']) as run:
            with self.assertRaisesRegex(ValueError, '2 <= min_samples <= max_samples'):
                example.measure(run, {**parameters, 'max_samples': 0})
            self.assertEqual(run.checkpoints(), {})
            with self.assertRaises(ConnectionError):
                example.measure(InterruptedClient(run, 'checkpoint', 1), parameters)
            self.assertEqual(run.info(False)['operation_count'], 0)
            self.assertEqual(run.streams(), [])
            self.assertEqual(run.checkpoints()['reference-measurement']['state']['completed'], 0)
            result = example.measure(run, parameters)
            self.assertEqual(result['samples'], 4)
            self.assertEqual(run.info(False)['operation_count'], 12)

    def test_generic_acquisition_package_continues_its_first_batch(self):
        self.setup_measurements()
        package = Path(self.temp.name) / 'first-batch-interruption'
        shutil.copytree(EXAMPLE.parent / 'resumable-acquisition', package)
        program = package / 'experiment.py'
        source = 'from pathlib import Path\n' + program.read_text(encoding='utf-8')
        source = source.replace('    stream.append(',
            '    marker = Path(os.environ["IOP_RUN_DIR"]) / "stopped-once"\n'
            '    if not marker.exists():\n'
            '        marker.write_text("first reading completed", encoding="utf-8")\n'
            '        raise RuntimeError("Interrupted after the first completed reading")\n'
            '    stream.append(', 1)
        program.write_text(source, encoding='utf-8')
        run = self.client.submit_package(package, resources=['signal'], parameters={
            'device': 'signal', 'operation': 'read', 'batches': 3})
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        self.assertEqual(run.info(False)['operation_count'], 1)
        original = run.operations()['operations'][0]['id']
        self.assertEqual(run.checkpoints()['main']['state'], {'next': 0})
        run.resume('continue-first-batch')
        self.assertEqual(run.result(15)['batches'], 3)
        self.assertEqual(run.info(False)['operation_count'], 3)
        self.assertEqual(run.operations()['operations'][0]['id'], original)
        self.assertEqual(run.stream('measurements').info()['chunks'], 3)

    def test_explicit_resume_replaces_only_a_failed_read_and_reuses_a_completed_replacement(self):
        parameters = self.setup_measurements(failed_reads=[2])
        package = Path(self.temp.name) / 'replacement-interruption'
        shutil.copytree(EXAMPLE, package)
        program = package / 'experiment.py'
        source = 'from pathlib import Path\n' + program.read_text(encoding='utf-8')
        source = source.replace('            # A reply lost here',
            '            marker = Path(os.environ["IOP_RUN_DIR"]) / "replacement-stopped-once"\n'
            '            if ":after:" in record["request_id"] and not marker.exists():\n'
            '                marker.write_text("replacement completed", encoding="utf-8")\n'
            '                raise RuntimeError("Interrupted after the replacement reading")\n'
            '            # A reply lost here', 1)
        program.write_text(source, encoding='utf-8')
        run = self.client.submit_package(package, resources=['signal', 'reference', 'dark'], parameters=parameters)
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        first = run.operations()['operations']
        self.assertEqual(len(first), 4)
        self.assertEqual([record['state'] for record in first], ['completed'] * 3 + ['failed'])
        self.assertEqual(run.checkpoints()['reference-measurement']['state']['completed'], 1)
        run.resume('retry-known-reading')
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        replaced = run.operations()['operations']
        self.assertEqual(len(replaced), 5)
        self.assertEqual(replaced[-1]['state'], 'completed')
        self.assertEqual(replaced[-1]['request_id'], first[-1]['request_id'] + ':after:' + first[-1]['id'])
        replacement_id = replaced[-1]['id']
        run.resume('continue-after-replacement')
        self.assertEqual(run.result(15)['stop'], 'precision')
        final = run.operations()['operations']
        self.assertEqual(len(final), 13)  # 12 completed readings and the retained failed one.
        self.assertEqual([record['id'] for record in final[:4]], [record['id'] for record in first])
        self.assertEqual(sum(record['id'] == replacement_id for record in final), 1)
        self.assertEqual(sum(record['state'] == 'failed' for record in final), 1)
        self.assertEqual(run.stream('reference-measurement').info()['chunks'], 4)

    def test_multiple_known_read_failures_require_separate_continuations(self):
        parameters = self.setup_measurements(failed_reads=[2, 3])
        run = self.client.submit_package(EXAMPLE, resources=['signal', 'reference', 'dark'], parameters=parameters)
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        self.assertEqual(run.info(False)['operation_count'], 4)
        run.resume('first-read-recovery')
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        self.assertEqual(run.info(False)['operation_count'], 5)
        run.resume('second-read-recovery')
        result = run.result(15)
        self.assertEqual(result['samples'], 4)
        self.assertAlmostEqual(result['estimate']['value'], 0.5)
        self.assertEqual(run.info(False)['operation_count'], 14)
        self.assertEqual(sum(record['state'] == 'failed' for record in run.operations()['operations']), 2)

    def test_unknown_measurement_outcome_never_gets_a_replacement_id(self):
        parameters = self.setup_measurements(unknown_reads=[2])
        run = self.client.submit_package(EXAMPLE, resources=['signal', 'reference', 'dark'], parameters=parameters)
        self.assertEqual(run.wait(15, details=False)['state'], 'failed')
        operations = run.operations()['operations']
        self.assertEqual(len(operations), 4)
        failed = operations[-1]
        self.assertTrue(failed['outcome_unknown'])
        with self.assertRaises(IOPError) as refused:
            run.resume('do-not-repeat-unknown-reading')
        self.assertEqual(refused.exception.code, 'device_outcome_unknown')
        with self.assertRaisesRegex(ValueError, 'measurement outcome is unknown'):
            example.read_request(failed['request_id'], {failed['request_id']: failed})
        self.assertEqual(run.info(False)['operation_count'], 4)

    def test_near_zero_reference_has_no_spurious_finite_ratio(self):
        parameters = self.setup_measurements()
        parameters['reference']['components'] = {'reference-drift': 20}
        with self.client.start_experiment(resources=['signal', 'reference', 'dark']) as run:
            result = example.measure(run, parameters)
            self.assertIsNone(result['estimate'])
            self.assertEqual(result['reason'], 'uncertain_reference')
            self.assertEqual(result['samples'], 6)
            self.assertFalse(result['precision_reached'])

    def test_parameters_and_units_cannot_change_on_continuation(self):
        parameters = self.setup_measurements()
        with self.client.start_experiment(resources=['signal', 'reference', 'dark']) as run:
            with self.assertRaises(ConnectionError):
                example.measure(InterruptedClient(run, 'checkpoint', 3), parameters)
            with self.assertRaisesRegex(ValueError, 'different parameters'):
                example.measure(run, {**parameters, 'max_samples': 20})
            self.assertEqual(run.info(False)['operation_count'], 6)
        parameters['signal']['unit'] = 'mW'
        with self.assertRaisesRegex(ValueError, 'same unit'):
            example.configuration(parameters)
        for name in ('signal', 'reference', 'dark'):
            parameters[name]['unit'] = 'mW'
        with self.client.start_experiment(resources=['signal', 'reference', 'dark']) as run:
            with self.assertRaisesRegex(ValueError, 'returned unit'):
                example.measure(run, parameters)


if __name__ == '__main__':
    unittest.main()
