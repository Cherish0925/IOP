"""Record batches from any registered instrument operation and continue after interruption."""
import json
import os
from ai4instruments.iop import Client

run = Client().current_run()
parameters = json.loads(os.environ['IOP_PARAMETERS_JSON'])
device, operation = parameters['device'], parameters['operation']
count = int(parameters.get('batches', 100))
progress = run.checkpoints().get('main', {}).get('state')
if progress is None:
    progress = {'next': 0}
    run.checkpoint(progress)  # The first batch can be interrupted too.
stream = run.stream('measurements', {'format': 'jsonl', 'device': device, 'operation': operation})
for index in range(progress['next'], count):
    # Retrying an already completed operation returns its original result.
    result = run.invoke(device, operation, parameters.get('arguments', {}), request_id=f'{run.id}-batch-{index}')
    stream.append((json.dumps(result, allow_nan=False) + '\n').encode(), index)
    run.checkpoint({'next': index + 1})
run.save_result({'batches': count, 'stream': 'measurements'})
