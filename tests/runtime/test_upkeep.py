"""Upkeep: a platform that declares it has its conditions kept by the Runtime while its instruments are idle."""
import time
import unittest
from datetime import datetime, timezone
from ai4instruments.iop import IOPError
from ai4instruments.iop.conditions import RENEWALS, upkeep
from . import test_service as fixture
from .test_conditions import PATH, SOURCE, WORLD, Part

DRIVER = Part.__module__ + ':Part'


def chain(*conditions, keep=True, **settings):
    """The signal chain of the condition cases; with ``keep`` it declares upkeep with these settings."""
    return {'schema_version': 1, 'id': 'chain', 'devices': ['source', 'coupler', 'detector'],
            'conditions': list(conditions), **({'upkeep': settings} if keep else {})}


class UpkeepCase(unittest.TestCase):
    def setUp(self):
        fixture.ServiceCase.setUp(self)
        self.engine.config['upkeep_interval_s'] = 0.05
        WORLD.update(level=1.0, alignment=1.0, x=0.0, y=0.0, x0=0.0, y0=0.0, log=[])
        for part in ('source', 'coupler', 'detector'):
            self.client.register({'id': part, 'driver': DRIVER, 'config': {'part': part}})

    tearDown = fixture.ServiceCase.tearDown

    def until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.02)
        self.fail('Upkeep did not reach the expected state')

    def states(self):
        return {row['id']: row['state'] for row in self.client.conditions('chain')}

    def last(self):
        status = self.client.upkeep('chain')[0]
        return status.get('last') if status.get('last', {}).get('state') not in (None, 'running') else None

    def test_without_upkeep_nothing_starts_by_itself(self):
        self.client.configure_platform(chain(SOURCE, PATH, keep=False))
        time.sleep(0.3)
        self.assertEqual((WORLD['log'], self.client.upkeep()), ([], []))
        self.assertEqual(set(self.states().values()), {'unknown'})

    def test_a_platform_is_brought_up_and_kept_in_its_working_state(self):
        self.client.configure_platform(chain(SOURCE, PATH, ahead_s=0))
        self.until(lambda: set(self.states().values()) == {'holds'})
        last = self.until(self.last)
        record = self.client.request('/v1/maintenance/' + last['id'])
        self.assertEqual((record['automatic'], record['reasons']), (True, {'source.stable': 'unknown', 'path.aligned': 'unknown'}))
        self.assertEqual(self.client.run(last['run_id']).info()['name'], 'Maintain chain (upkeep)')
        # The table was bumped: the Runtime notices the condition is due and restores it while nobody is working.
        WORLD['alignment'] = 0.5
        self.client.invalidate(['path.aligned'], 'the table was bumped')
        self.until(lambda: self.states()['path.aligned'] == 'holds' and WORLD['alignment'] == 1.0)
        corrected = [r for r in self.client.condition('path.aligned')['records'] if r['type'] == 'corrected']
        self.assertEqual((corrected[-1]['by'], corrected[-1]['restored']), ('coupler.peak_up', True))
        self.assertIn('last', self.client.context()['setup']['upkeep']['chain'])

    def test_upkeep_waits_while_an_experiment_uses_the_instruments(self):
        self.client.configure_platform(chain(SOURCE, PATH))
        self.until(lambda: set(self.states().values()) == {'holds'})
        experiment = self.client.start_experiment(platform='chain', resources=['detector'])
        self.client.invalidate(['path.aligned'], 'the enclosure was opened')
        reads = WORLD['log'].count('detector.read')
        waiting = self.until(lambda: self.client.upkeep('chain')[0].get('waiting'))
        self.assertEqual(waiting, 'waiting for experiment ' + experiment.id)
        time.sleep(0.2)
        self.assertEqual(WORLD['log'].count('detector.read'), reads)
        experiment.finish()
        self.until(lambda: self.states()['path.aligned'] == 'holds')

    def test_validity_is_renewed_before_it_ends(self):
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 1.0}, ahead_s=0.6))
        self.until(lambda: set(self.states().values()) == {'holds'})

        def renewed():
            # On a slow machine the pass that brought the platform up may still be the last one for a while.
            job = self.last()
            found = job and self.client.request('/v1/maintenance/' + job['id'])
            return found if found and found['reasons'] == {'path.aligned': 'validity ends'} else None
        self.assertTrue(self.until(renewed))
        self.assertEqual(self.states()['path.aligned'], 'holds')

    def test_a_lost_condition_is_attempted_again_after_retry_s(self):
        fixed = {k: v for k, v in SOURCE.items() if k != 'correct'}
        WORLD['level'] = 0.5
        # retry_s leaves room for a slow machine to finish the first pass well before the next is due.
        self.client.configure_platform(chain(fixed, retry_s=2))
        self.until(lambda: self.states()['source.stable'] == 'lost')
        reads = WORLD['log'].count('source.read')
        time.sleep(0.3)
        self.assertEqual(WORLD['log'].count('source.read'), reads)
        self.assertEqual(self.client.upkeep('chain')[0]['pending'], {})
        WORLD['level'] = 1.0
        self.until(lambda: self.states()['source.stable'] == 'holds')

    def test_automatic_passes_leave_steps_that_ask_a_person(self):
        self.client.register({'id': 'bench', 'driver': 'manual',
                              'config': {'operations': {'align_by_hand': {'instruction': 'Align the coupler by hand'}}}})
        by_hand = {**PATH, 'correct': [{'device': 'bench', 'operation': 'align_by_hand'}]}
        WORLD['alignment'] = 0.5
        self.client.configure_platform({**chain(SOURCE, by_hand), 'devices': ['source', 'coupler', 'detector', 'bench']})
        last = self.until(self.last)
        entry = {e['condition']: e for e in self.client.request('/v1/maintenance/' + last['id'])['report']}['path.aligned']
        self.assertEqual((entry['result'], entry['left_for_a_person']), ('lost', ['bench.align_by_hand']))
        self.assertEqual(self.client.manual_tasks(), [])

    def test_upkeep_can_observe_without_correcting(self):
        WORLD['alignment'] = 0.5
        self.client.configure_platform(chain(SOURCE, PATH, correct=False))
        last = self.until(self.last)
        entry = {e['condition']: e for e in self.client.request('/v1/maintenance/' + last['id'])['report']}['path.aligned']
        self.assertEqual((entry['result'], entry['not_corrected']), ('lost', 'upkeep observes only'))
        self.assertNotIn('coupler.peak_up', WORLD['log'])

    def test_the_drift_forecast_decides_when_to_look_again(self):
        self.client.configure_platform(chain(SOURCE, keep=False))
        for level in (1.0, 1.01, 1.02):
            WORLD['level'] = level
            self.client.maintain('chain', force=True)
        held = self.client.condition('source.stable')
        self.assertGreater(held['forecast'], held['accepted'])
        # The line through the accepted values crosses the tolerance soon, as soon as the three verifications
        # followed each other on this machine: upkeep looks again then.
        self.client.configure_platform(chain(SOURCE, ahead_s=3600), expected_revision=1)
        crossing = datetime.fromisoformat(held['forecast']).timestamp()
        last = self.until(self.last, timeout=5 + max(0.0, crossing - time.time()))
        self.assertEqual(self.client.request('/v1/maintenance/' + last['id'])['reasons'],
                         {'source.stable': 'forecast reaches the tolerance'})

    def test_each_foreseen_moment_triggers_one_observation(self):
        from ai4instruments.iop.conditions import seconds
        stamp = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat()
        self.client.configure_platform(chain(SOURCE, ahead_s=60))
        self.until(lambda: self.states()['source.stable'] == 'holds')
        conditions = self.engine.conditions
        with self.engine.lock:
            saved = conditions.states['source.stable']
            accepted = seconds(saved['accepted'])
            saved['forecast'] = stamp(accepted + 100)
            # Before the first moment nothing is due; the plan names when it comes.
            early = conditions.plan('chain', at=accepted + 30)
            self.assertEqual((early['reasons'], early['next']), ({}, stamp(accepted + 40)))
            self.assertEqual(conditions.plan('chain', at=accepted + 50)['reasons'],
                             {'source.stable': 'forecast reaches the tolerance'})
            # An observation after the first moment leaves only the crossing itself.
            saved['accepted'] = stamp(accepted + 45)
            later = conditions.plan('chain', at=accepted + 50)
            self.assertEqual((later['reasons'], later['next']), ({}, stamp(accepted + 100)))
            self.assertEqual(conditions.plan('chain', at=accepted + 101)['reasons'],
                             {'source.stable': 'forecast reaches the tolerance'})
            saved['forecast'] = None

    def test_an_experiment_keeps_what_it_relies_on_before_its_operations(self):
        # Only the operations of the experiment start passes here, not the periodic plan.
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 0.4}, keep=False))
        # Not yet observed: the experiment is accepted and its conditions are brought up inside it first.
        experiment = self.client.start_experiment(platform='chain', resources=['source', 'coupler', 'detector'],
                                                  requires=['path.aligned'], upkeep=True)
        read = [{'id': 'read', 'device': 'detector', 'operation': 'read'}]
        self.assertEqual(experiment.batch(read)[0]['result']['signal'], 1.0)
        # The validity ran out and the coupling drifted: the next operation waits while the Runtime restores it.
        time.sleep(0.45)
        WORLD['alignment'] = 0.5
        self.assertEqual(experiment.batch(read)[0]['result']['signal'], 1.0)
        info = experiment.info()
        upkept = [op for op in self.client.request(f'/v1/runs/{experiment.id}/operations')['operations'] if op.get('maintenance')]
        self.assertIn('coupler', {op['device'] for op in upkept})
        self.assertEqual(info['in_doubt']['path.aligned']['outcome'], 'drifted')
        experiment.finish()

    def test_between_operations_an_experiment_renews_its_conditions_ahead_of_time(self):
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 1.0}, keep=False))
        self.client.maintain('chain')
        experiment = self.client.start_experiment(platform='chain', resources=['source', 'coupler', 'detector'],
                                                  requires=['path.aligned'], upkeep={'ahead_s': 0.6})
        experiment.batch([{'id': 'read', 'device': 'detector', 'operation': 'read'}])
        first = self.client.condition('path.aligned')['accepted']
        self.until(lambda: self.client.condition('path.aligned')['accepted'] != first)

        def finished():
            row = next((r for r in self.client.upkeep() if r.get('run') == experiment.id), {})
            return row.get('last') if row.get('last', {}).get('state') not in (None, 'running') else None
        renewal = self.client.request('/v1/maintenance/' + self.until(finished)['id'])
        self.assertEqual((renewal['run_id'], renewal['automatic'], renewal['reasons']),
                         (experiment.id, True, {'path.aligned': 'validity ends'}))
        experiment.finish()

    def test_a_step_graph_keeps_what_it_relies_on_between_its_operations(self):
        # Only the step graph starts passes here, not the periodic plan.
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 0.3}, keep=False))
        # Not yet observed, and the coupling fades while the detector integrates: six readings of 0.15 s
        # outlast the alignment's validity and its tolerance.
        WORLD['fade'] = 0.6
        self.addCleanup(WORLD.pop, 'fade')
        steps = [{'id': 'scan', 'device': 'detector', 'operation': 'integrate', 'arguments': {'seconds': 0.15}, 'repeat': 6}]
        # One reserves what its steps use, and with it what keeps the alignment; one claims each instrument in turn.
        for resources in (['detector'], []):
            run = self.client.submit(steps, platform='chain', resources=resources, requires=['path.aligned'], upkeep=True)
            info = run.wait(timeout=30)
            self.assertEqual(info['state'], 'completed')
            operations = info['operation_records']
            points = [op for op in operations if op['step'].startswith('scan.')]
            upkept = [op for op in operations if op.get('maintenance')]
            # Renewed between readings and realigned when found drifted.
            self.assertEqual(len(points), 6)
            self.assertGreaterEqual(len({op['conditions']['path.aligned']['accepted'] for op in points}), 2)
            self.assertIn('coupler', {op['device'] for op in upkept})
            self.assertEqual(info['in_doubt']['path.aligned']['outcome'], 'drifted')
            # A reading is in doubt when a later verification found the alignment drifted, so each faded one
            # before the end is; those relying on the last verification are not.
            last = max(op['conditions']['path.aligned']['accepted'] for op in points)
            for op in points:
                self.assertEqual('path.aligned' in op.get('in_doubt', {}), op['conditions']['path.aligned']['accepted'] != last)
            self.assertTrue(all(op.get('in_doubt') for op in points[:-1] if op['result']['signal'] < 0.8))
            self.assertEqual([op['id'] for op in run.operations(in_doubt=True)['operations']],
                             [op['id'] for op in sorted(operations, key=lambda op: op['created']) if op.get('in_doubt')])
            self.assertEqual(list(info['result']), ['scan'])
            self.assertEqual({'device:coupler', 'device:source'} <= set(info['resources']), bool(resources))

    def test_a_sweep_takes_again_the_points_a_drift_put_in_doubt(self):
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 0.3}, keep=False))
        # The table is bumped during the third of six readings: the coupling halves until upkeep finds it.
        WORLD.update(bump=3, integrations=0)
        self.addCleanup(WORLD.pop, 'bump')
        run = self.client.submit([{'id': 'scan', 'device': 'detector', 'operation': 'integrate', 'arguments': {'seconds': 0.15},
                                   'repeat': 6, 'retake': True}], platform='chain', requires=['path.aligned'], upkeep=True)
        info = run.wait(timeout=30)
        self.assertEqual(info['state'], 'completed')
        operations = {op['id']: op for op in info['operation_records']}
        retakes = [op for op in operations.values() if op.get('retakes')]
        # What the sweep gathered holds a full signal at each point: those read through the bump were taken again
        # once the coupling was restored, before the next point, and their first readings stay in their records.
        self.assertEqual(run.result(10)['scan']['signal']['values'], [1.0] * 6)
        self.assertTrue(retakes)
        for retake in retakes:
            original = operations[retake['retakes']]
            self.assertEqual((original['retaken_by'], retake['step'], retake['arguments']),
                             (retake['id'], original['step'] + '~1', original['arguments']))
            self.assertIn('path.aligned', original['in_doubt'])
            self.assertNotIn('in_doubt', retake)
        self.assertIn(0.5, [op['result']['signal'] for op in operations.values() if op.get('retaken_by')])
        with self.assertRaisesRegex(IOPError, 'retake takes again the points of a device operation'):
            self.client.submit([{'id': 'twice', 'operation': 'missing.saved', 'repeat': 2, 'retake': True}])
        # A program decides itself what to take again.
        with self.client.start_experiment(resources=['detector']) as experiment, \
                self.assertRaisesRegex(IOPError, 'retake takes again the points of a sweep in a step graph'):
            experiment.batch([{'id': 'scan', 'device': 'detector', 'operation': 'read', 'repeat': 2, 'retake': True}])

    def test_retakes_do_not_replay_actions_whose_arguments_came_from_doubtful_data(self):
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, PATH, keep=False))
        self.client.maintain('chain')
        run = self.client.submit([
            {'id': 'scan', 'device': 'detector', 'operation': 'read', 'repeat': 2, 'retake': True},
            {'id': 'steer', 'device': 'stage', 'operation': 'move_to', 'arguments': {'duration': 0},
             'repeat': 2, 'retake': True,
             'bindings': {'angle_deg': {'step': 'scan', 'view': {'field': 'signal'}, 'path': ['summary', 'mean']}}},
            {'id': 'wait', 'device': 'aux', 'operation': 'wait', 'arguments': {'duration': 2}, 'after': ['steer']},
        ], requires=['path.aligned'], upkeep=True)
        self.until(lambda: any(op['step'] == 'wait' and op['state'] == 'running'
                               for op in run.operations()['operations']))
        # The experiment is still active. A verification discovers drift since before scan and steer ran.
        WORLD['alignment'] = 0.5
        self.client.maintain('chain', force=True)
        info = run.wait(timeout=15)
        self.assertEqual(info['state'], 'completed', info.get('error'))
        operations = info['operation_records']
        self.assertTrue(any(op.get('retakes') and op['step'].startswith('scan.') for op in operations))
        steering = [op for op in operations if op['step'].startswith('steer.')]
        self.assertEqual(len(steering), 2)
        self.assertTrue(all(op['in_doubt']['path.aligned']['source_operations'] for op in steering))
        self.assertTrue(all('retaken_by' not in op and 'retakes' not in op for op in steering))
        self.assertEqual(run.result(5)['scan']['signal']['values'], [1.0, 1.0])

    def test_a_pass_runs_between_the_points_taken_again(self):
        # The table is bumped during the third reading and shakes during the fourth, so the four readings since the
        # alignment was verified are in doubt; it shakes again while the first of them is taken again. The pass that
        # renews the alignment runs before the next retake, and the retakes wait for nothing else.
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, PATH, keep=False))
        WORLD.update(bump=3, shaken=(4, 5), integrations=0, engine=self.engine)
        self.addCleanup(lambda: [WORLD.pop(key) for key in ('bump', 'shaken', 'engine')])
        run = self.client.submit([{'id': 'scan', 'device': 'detector', 'operation': 'integrate', 'arguments': {'seconds': 0.05},
                                   'repeat': 6, 'retake': True}], platform='chain', requires=['path.aligned'], upkeep=True)
        info = run.wait(timeout=60)
        self.assertEqual(info['state'], 'completed', info.get('error'))
        self.assertEqual(run.result(10)['scan']['signal']['values'], [1.0] * 6)
        retakes = sorted((op for op in info['operation_records'] if op.get('retakes')), key=lambda op: op['started'])
        self.assertEqual(len(retakes), 4)
        passes = [op['started'] for op in info['operation_records'] if op.get('maintenance') and op['operation'] == 'read']
        self.assertTrue(any(retakes[0]['ended'] < started < retakes[1]['started'] for started in passes))

    def test_a_validity_too_short_to_keep_ends_the_experiment_with_the_reason(self):
        # An alignment valid for a microsecond is due again before any operation can start after its verification:
        # upkeep renews it three times, then the experiment ends and says why, where it went on renewing forever.
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 1e-6}, keep=False))
        run = self.client.submit([{'id': 'read', 'device': 'detector', 'operation': 'integrate', 'arguments': {'seconds': 0}}],
                                 platform='chain', requires=['path.aligned'], upkeep=True)
        info = run.wait(timeout=30)
        self.assertEqual(info['state'], 'failed')
        self.assertRegex(info['error'], 'path.aligned is due .* renewed what this experiment requires 3 times in a row '
                                        '.* Review maintenance and acquisition timing')
        events = self.client.request(f'/v1/events?run_id={run.id}&limit=1000')['events']
        self.assertEqual(sum(event['kind'] == 'upkeep.started' for event in events), 3)
        self.assertNotIn('detector.integrate', WORLD['log'])
        # Continuation starts a new maintenance budget, without silently extending the physical validity.
        run.resume('retry-maintenance')
        self.assertEqual(run.wait(timeout=30)['state'], 'failed')
        events = self.client.request(f'/v1/events?run_id={run.id}&limit=1000')['events']
        self.assertEqual(sum(event['kind'] == 'upkeep.started' for event in events), 6)
        self.assertNotIn('detector.integrate', WORLD['log'])

    def test_an_experiment_is_kept_for_as_long_as_it_has_nothing_to_start(self):
        # Renewals are counted against an operation that is free to start. An experiment that is open with nothing
        # to run, or whose operation waits for something another experiment holds, is kept for any time.
        self.client.configure_platform(chain(SOURCE, {**PATH, 'valid_for': 1.5}, keep=False))

        def passes(run):
            events = self.client.request(f'/v1/events?run_id={run.id}&limit=1000')['events']
            return sum(event['kind'] == 'upkeep.started' for event in events)
        read = {'id': 'read', 'device': 'detector', 'operation': 'read'}
        open_ = self.client.start_experiment(platform='chain', resources=['source', 'coupler', 'detector'],
                                             requires=['path.aligned'], upkeep={'ahead_s': 0.5})
        self.until(lambda: passes(open_) > RENEWALS, 30)
        self.assertEqual(open_.batch([read])[0]['result']['signal'], 1.0)
        open_.finish()
        holder = self.client.start_experiment(resources=['sample'])
        waiting = self.client.submit([{**read, 'resources': ['sample']}], platform='chain', requires=['path.aligned'],
                                     upkeep={'ahead_s': 0.5})
        self.until(lambda: passes(waiting) > RENEWALS, 30)
        self.assertEqual([op['state'] for op in waiting.operations()['operations'] if op['step'] == 'read'], ['queued'])
        holder.finish()
        self.assertEqual(waiting.result(30)['read']['signal'], 1.0)

    def test_rejected_operations_do_not_start_instrument_maintenance(self):
        from ai4instruments.iop import batches
        self.engine.config['upkeep_interval_s'] = 3600
        self.client.configure_platform(chain(SOURCE, PATH, keep=False))
        self.client.maintain('chain')
        experiment = self.client.start_experiment(platform='chain', resources=['source', 'coupler', 'detector'],
                                                  requires=['path.aligned'], upkeep=True)
        with self.engine.lock:
            self.engine.conditions.invalidate(['path.aligned'], 'alignment changed')
            jobs = set(self.engine.conditions.jobs)
            for request in ({'device': 'detector', 'operation': 'missing'},
                            {'device': 'detector', 'operation': 'read', 'after': ['prior']}):
                with self.assertRaises((ValueError, KeyError)):
                    self.engine.invoke(experiment.id, request)
                self.assertEqual(set(self.engine.conditions.jobs), jobs)
            with self.assertRaises((ValueError, KeyError)):
                batches.submit(self.engine, experiment.id, [{'id': 'bad', 'device': 'detector', 'operation': 'missing'}],
                               'invalid-batch')
            self.assertEqual(set(self.engine.conditions.jobs), jobs)
        experiment.finish()

    def test_an_experiment_that_keeps_its_conditions_reserves_what_maintenance_uses(self):
        self.client.configure_platform(chain(SOURCE, PATH, keep=False))
        from ai4instruments.iop import IOPError
        with self.assertRaisesRegex(IOPError, 'name them in requires'):
            self.client.start_experiment(platform='chain', resources=['detector'], upkeep=True)
        with self.assertRaises(IOPError) as refused:
            self.client.start_experiment(platform='chain', resources=['detector'], requires=['path.aligned'], upkeep=True)
        self.assertEqual((refused.exception.code, sorted(refused.exception.details['resources'])),
                         ('maintenance_resources', ['device:coupler', 'device:source']))

    def test_settings_are_checked(self):
        self.assertEqual(upkeep({}, ['a']), {'conditions': ['a'], 'ahead_s': 0.0, 'retry_s': 1800.0, 'correct': True})
        for value, message in (({'conditions': ['b']}, 'name conditions of this platform'), ({'ahead_s': -1}, 'duration'),
                               ({'correct': 'no'}, 'observe without correcting'), ({'every': 5}, 'takes conditions')):
            with self.assertRaisesRegex(ValueError, message):
                upkeep(value, ['a'])


if __name__ == '__main__':
    unittest.main()
