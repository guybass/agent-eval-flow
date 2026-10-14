"""Native RPC byte-stream fidelity probes; expected values never require a model."""
import base64
import hashlib
import json
from pathlib import Path
import selectors
import subprocess
import time

OUT = Path('/evidence')
OUT.mkdir(exist_ok=True)
cases = [{'id': 'plain-control', 'chunks': [b'ERROR: file.py:1\n'], 'fd': 1, 'expected': 'ERROR: file.py:1\n'}, {'id': 'ansi-unsplit-control', 'chunks': [b'\x1b[31mERROR: file.py:1\x1b[0m\n'], 'fd': 1, 'expected': 'ERROR: file.py:1\n'}, {'id': 'ansi-split-after-escape', 'chunks': [b'\x1b', b'[31mERROR: file.py:1\x1b[0m\n'], 'fd': 1, 'expected': 'ERROR: file.py:1\n'}, {'id': 'ansi-split-after-bracket', 'chunks': [b'\x1b[', b'31mERROR: file.py:1\x1b[0m\n'], 'fd': 1, 'expected': 'ERROR: file.py:1\n'}, {'id': 'ansi-split-inside-parameter', 'chunks': [b'\x1b[3', b'1mERROR: file.py:1\x1b[0m\n'], 'fd': 1, 'expected': 'ERROR: file.py:1\n'}, {'id': 'ansi-split-reset', 'chunks': [b'\x1b[31mERROR: file.py:1\x1b[0', b'm\n'], 'fd': 1, 'expected': 'ERROR: file.py:1\n'}]
version = subprocess.check_output(['/opt/pi/pi', '--version'], text=True).strip()
freeze = {'version': version, 'new_model_calls': 0,
          'cases': [{'id': c['id'], 'chunks_hex': [x.hex() for x in c['chunks']], 'fd': c['fd'], 'expected': c['expected']} for c in cases],
          'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'split': 'development component probes; no generalization claim'}
(OUT / 'pre-run-freeze.json').write_text(json.dumps(freeze, indent=2))
with (OUT / 'stderr.log').open('wb') as err, (OUT / 'rpc.jsonl').open('wb') as raw:
    process = subprocess.Popen(['/opt/pi/pi', '--offline', '--mode', 'rpc', '--no-session',
        '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-themes', '--no-context-files'],
        cwd='/work', stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    results = []
    try:
        for case in cases:
            code = 'import os,time\n'
            for chunk in case['chunks']:
                code += f"os.write({case['fd']}, bytes.fromhex('{chunk.hex()}')); time.sleep(0.15)\n"
            encoded = base64.b64encode(code.encode()).decode()
            shell = "python -c \"import base64; exec(base64.b64decode('" + encoded + "'))\""
            process.stdin.write(json.dumps({'id': case['id'], 'type': 'bash', 'command': shell}).encode() + b'\n')
            process.stdin.flush()
            deadline = time.monotonic() + 20
            events = []
            while True:
                if not selector.select(timeout=max(0, deadline - time.monotonic())):
                    raise TimeoutError('RPC response deadline')
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError('Native process exited')
                raw.write(line)
                raw.flush()
                row = json.loads(line)
                events.append(row)
                if row.get('type') == 'response' and row.get('id') == case['id']:
                    break
            expected = case['expected']
            actual = row.get('data', {}).get('output')
            streamed = ''.join(r.get('delta', '') for r in events if r.get('type') == 'bash_execution_update')
            results.append({'case': case['id'], 'success': row.get('success'),
                'exit_code': row.get('data', {}).get('exitCode'), 'expected': expected, 'actual': actual,
                'streamed': streamed, 'exact_returned_text': actual == expected,
                'exact_streamed_text': streamed == expected, 'truncated': row.get('data', {}).get('truncated')})
        process.stdin.write(json.dumps({'id': 'retained-messages', 'type': 'get_messages'}).encode() + b'\n')
        process.stdin.flush()
        deadline = time.monotonic() + 20
        while True:
            if not selector.select(timeout=max(0, deadline - time.monotonic())):
                raise TimeoutError('Retained-message response deadline')
            line = process.stdout.readline()
            if not line:
                raise RuntimeError('Native process exited')
            raw.write(line)
            raw.flush()
            row = json.loads(line)
            if row.get('type') == 'response' and row.get('id') == 'retained-messages':
                break
        (OUT / 'messages.json').write_text(json.dumps(row, indent=2))
        retained = [m for m in row['data']['messages'] if m.get('role') == 'bashExecution']
        assert len(retained) == len(results)
        for result, message in zip(results, retained):
            assert result['actual'] == message['output']
            result['retained_conversation_matches_returned_output'] = True
        (OUT / 'results.json').write_text(json.dumps({'configuration': freeze, 'cases': results}, indent=2))
        print(json.dumps({'version': version, 'cases': results, 'new_model_calls': 0}), flush=True)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
