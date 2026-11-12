"""The adapter reference an agent reads matches what the drivers accept."""
import unittest
from ai4instruments.iop.adapter_reference import COMMON, REFERENCE, reference
from ai4instruments.iop.drivers import make_driver
from ai4instruments.iop.plugins import adapters
from ai4instruments.iop.python_driver import check
from . import test_service as fixture


def documented(fields):
    return {name.strip() for key in fields for name in key.replace('|', ',').split(',')}


class ReferenceCase(unittest.TestCase):
    def test_every_described_adapter_is_installed_and_examples_use_documented_fields(self):
        installed = {adapter['name'] for adapter in adapters()}
        self.assertLessEqual(set(REFERENCE), installed)
        for name, entry in REFERENCE.items():
            example = entry['example']
            self.assertEqual(example['driver'], name)
            connection = {'host', 'port', 'baudrate', 'resource'} if name in ('scpi', 'modbus') else set()
            self.assertLessEqual(set(example['config']), documented(entry['config']) | connection, name)
            for operation in example['config'].get('operations', {}).values():
                self.assertLessEqual(set(operation), documented(entry['operation']) | set(COMMON), name)

    def test_examples_are_accepted_by_their_drivers(self):
        # These adapters build their description without contacting the instrument.
        for name in ('scpi', 'modbus', 'http', 'manual', 'command'):
            driver = make_driver(REFERENCE[name]['example'], {})
            try:
                self.assertLessEqual(set(REFERENCE[name]['example']['config']['operations']), set(driver.describe()['operations']))
            finally:
                driver.close()
        for operation, spec in REFERENCE['python']['example']['config']['operations'].items():
            check(operation, spec)
        self.assertEqual(reference('opcua')['operation'], {})
        self.assertIn('effect', reference('scpi')['operation'])


class EntryPointCase(unittest.TestCase):
    setUp = fixture.ServiceCase.setUp
    tearDown = fixture.ServiceCase.tearDown

    def test_one_adapter_is_read_by_name(self):
        entry = self.client.adapters('python')
        self.assertEqual((entry['name'], entry['example']['driver']), ('python', 'python'))
        self.assertIn('allowed_operations', entry['record'])
        self.assertIsInstance(self.client.adapters(), list)
        with self.assertRaises(Exception):
            self.client.adapters('absent')


if __name__ == '__main__':
    unittest.main()
