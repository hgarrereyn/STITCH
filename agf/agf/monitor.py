import argparse
import json
import time
import threading
import logging
from pathlib import Path
from collections import defaultdict
from flask import Flask, render_template, jsonify

import importlib.util
import traceback
import tempfile
import os

# Configure logging to avoid Flask clutter
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Initialize Flask with correct template folder
template_dir = Path(__file__).parent / 'templates'
app = Flask(__name__, template_folder=str(template_dir))

# Global state
state = {
    'project_path': None,
    'harness_dir': None,
    'stats': {
        'total_execs': 0,
        'unique_graphs': set(),
        'endpoint_stats': defaultdict(lambda: {'bails': 0, 'success': 0, 'skip': 0, 'total': 0}),
        'recent_logs': []
    },
    'endpoints': [],
    'generators': [],
    'data_types': [],
    'endpoint_classifiers': {},
    'harness_name': '',
    'revision': 0,
    'corpus_index': {
        'files': {}, # filename -> {type, endpoints: []}
        'by_endpoint': defaultdict(list), # endpoint_id -> [filenames]
        'counts': defaultdict(int) # endpoint_id -> count
    }
}

def sample_generator(generator_code: str, n: int = 10) -> list[bytes]:
    """Sample a generator function"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(generator_code)
        temp_file_path = f.name
    
    try:
        spec = importlib.util.spec_from_file_location("temp_generator", temp_file_path)
        if spec is None or spec.loader is None:
            raise Exception("Failed to load module")
            
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        if not hasattr(module, 'generate'):
            raise Exception("Generator function not found")
            
        generator_func = getattr(module, 'generate')
        
        outputs = []
        for i in range(n):
            try:
                output = generator_func(seed=i)
                outputs.append(output)
            except Exception:
                continue
        return outputs
    finally:
        try:
            os.unlink(temp_file_path)
        except OSError:
            pass

def load_harness_data(harness_path):
    try:
        with open(harness_path, 'r') as f:
            data = json.load(f)
            endpoints = []
            for i, ep in enumerate(data.get('endpoints', [])):
                endpoints.append({
                    'id': i,
                    'name': ep['name'],
                    'code': ep.get('code', ''),
                    'inputs': ep.get('inputs', []),
                    'outputs': ep.get('outputs', [])
                })
            
            # Load additional metadata
            generators = data.get('generators', [])
            data_types = data.get('data_types', [])
            
            # Process classifiers into a dict: endpoint_name -> list of types
            classifiers = {}
            for c in data.get('endpoint_classifiers', []):
                classifiers[c['name']] = c['data_types']

            return endpoints, generators, data_types, classifiers
    except Exception as e:
        print(f"Error loading harness: {e}")
        return [], [], [], {}

def index_corpus_file(file_path, filename, ftype):
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            # Extract endpoints used in this graph
            # data['graph']['nodes'] -> each has 'typ'
            endpoints = set()
            node_count = 0
            if 'graph' in data and 'nodes' in data['graph']:
                nodes = data['graph']['nodes']
                node_count = len(nodes)
                for node in nodes:
                    endpoints.add(node['typ'])
            
            return list(endpoints), node_count
    except Exception:
        return [], 0

def corpus_indexer_thread():
    """Background thread to index corpus files"""
    while True:
        try:
            if not state['harness_dir']:
                time.sleep(1)
                continue

            campaign_dir = state['harness_dir'] / 'campaign'
            revisions_dir = campaign_dir / 'revisions'
            
            if not revisions_dir.exists():
                time.sleep(1)
                continue
            
            # Find latest revision
            revisions = sorted([x for x in revisions_dir.iterdir() if x.is_dir() and x.name.isdigit()], key=lambda x: int(x.name))
            if not revisions:
                time.sleep(1)
                continue
            
            latest_rev = revisions[-1]
            out_dir = latest_rev / 'outputs'
            
            # Scan queue and crashes
            for subdir in ['queue', 'crashes']:
                d = out_dir / subdir
                if not d.exists():
                    continue
                    
                for f in d.iterdir():
                    if f.is_file() and not f.name.startswith('.'):
                        if f.name not in state['corpus_index']['files']:
                            # New file, index it
                            eps, size = index_corpus_file(f, f.name, subdir)
                            state['corpus_index']['files'][f.name] = {
                                'type': subdir,
                                'endpoints': eps,
                                'size': size
                            }
                            
                            # Update reverse index
                            for ep in eps:
                                state['corpus_index']['by_endpoint'][ep].append(f.name)
                                state['corpus_index']['counts'][ep] += 1
            
            time.sleep(2) # Check every 2 seconds
            
        except Exception as e:
            print(f"Error in corpus indexer: {e}")
            time.sleep(2)

def log_reader_thread():
    """Background thread to read logs and update stats"""
    current_log_file = None
    last_file_size = 0
    
    while True:
        try:
            if not state['harness_dir']:
                time.sleep(1)
                continue

            campaign_dir = state['harness_dir'] / 'campaign'
            revisions_dir = campaign_dir / 'revisions'
            
            if not revisions_dir.exists():
                time.sleep(1)
                continue
            
            # Find latest revision
            revisions = sorted([x for x in revisions_dir.iterdir() if x.is_dir() and x.name.isdigit()], key=lambda x: int(x.name))
            if not revisions:
                time.sleep(1)
                continue
            
            latest_rev = revisions[-1]
            state['revision'] = int(latest_rev.name)
            
            # Check if we need to reload harness data (new revision)
            harness_json = latest_rev / 'harness.json'
            if harness_json.exists():
                # We only reload if we haven't loaded or if it's a new revision
                if not state['endpoints']:
                    eps, gens, dts, clfs = load_harness_data(harness_json)
                    state['endpoints'] = eps
                    state['generators'] = gens
                    state['data_types'] = dts
                    state['endpoint_classifiers'] = clfs

            log_file = latest_rev / 'outputs' / 'viz_stats.jsonl'
            
            if not log_file.exists():
                time.sleep(1)
                continue
                
            if current_log_file != log_file:
                current_log_file = log_file
                last_file_size = 0
                
            with open(log_file, 'r') as f:
                f.seek(last_file_size)
                lines = f.readlines()
                last_file_size = f.tell()
                
                for line in lines:
                    try:
                        data = json.loads(line)
                        if data['type'] == 'graph_exec':
                            state['stats']['total_execs'] += 1
                            state['stats']['unique_graphs'].add(data['hash'])
                            
                            result = data['result']
                            node_types = data.get('node_types', [])
                            bail_node_type = data.get('bail_node_type')
                            
                            # Parse new endpoint execution tracking
                            executed_endpoints = data.get('executed_endpoints', [])
                            bailed_endpoint = data.get('bailed_endpoint')
                            skipped_endpoints = data.get('skipped_endpoints', [])
                            
                            # Update success count for executed endpoints
                            for ep in executed_endpoints:
                                state['stats']['endpoint_stats'][ep]['success'] += 1
                                state['stats']['endpoint_stats'][ep]['total'] += 1
                            
                            # Update bails count for bailed endpoint
                            if bailed_endpoint is not None:
                                state['stats']['endpoint_stats'][bailed_endpoint]['bails'] += 1
                                state['stats']['endpoint_stats'][bailed_endpoint]['total'] += 1
                            
                            # Update skip count for skipped endpoints
                            for ep in skipped_endpoints:
                                state['stats']['endpoint_stats'][ep]['skip'] += 1
                                state['stats']['endpoint_stats'][ep]['total'] += 1

                            # Add to recent logs (keep last 50)
                            log_entry = {
                                'time': time.time(),
                                'hash': data['hash'],
                                'result': result,
                                'bail_node': bail_node_type,
                                'nodes': len(node_types)
                            }
                            state['stats']['recent_logs'].append(log_entry)
                            if len(state['stats']['recent_logs']) > 50:
                                state['stats']['recent_logs'].pop(0)
                                
                    except json.JSONDecodeError:
                        pass
            
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Error in log reader: {e}")
            time.sleep(1)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    # Convert sets to lists for JSON serialization
    stats_copy = {
        'total_execs': state['stats']['total_execs'],
        'unique_graphs': len(state['stats']['unique_graphs']),
        'endpoint_stats': state['stats']['endpoint_stats'],
        'recent_logs': list(reversed(state['stats']['recent_logs']))
    }
    
    return jsonify({
        'harness_name': state['harness_name'],
        'revision': state['revision'],
        'stats': stats_copy,
        'endpoints': state['endpoints'],
        'generators': state['generators'],
        'data_types': state['data_types'],
        'endpoint_classifiers': state['endpoint_classifiers']
    })

@app.route('/api/generate_sample')
def generate_sample():
    from flask import request
    data_type = request.args.get('type')
    if not data_type:
        return jsonify({'error': 'Missing type'}), 400
        
    # Find generator
    generator = next((g for g in state['generators'] if g['data_type'] == data_type), None)
    if not generator:
        return jsonify({'error': 'Generator not found'}), 404
        
    try:
        samples = sample_generator(generator['generator'], n=5)
        return jsonify({
            'samples': [
                {
                    'text': s.decode('utf-8', errors='replace'),
                    'hex': s.hex()
                } for s in samples
            ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/corpus')
def get_corpus_list():
    if not state['harness_dir']:
        return jsonify({'queue': [], 'crashes': [], 'index': {}, 'files': {}})
        
    campaign_dir = state['harness_dir'] / 'campaign'
    revisions_dir = campaign_dir / 'revisions'
    
    if not revisions_dir.exists():
        return jsonify({'queue': [], 'crashes': [], 'index': {}, 'files': {}})
        
    # Use latest revision
    latest_rev = sorted([x for x in revisions_dir.iterdir() if x.is_dir() and x.name.isdigit()], key=lambda x: int(x.name))[-1]
    out_dir = latest_rev / 'outputs'
    
    queue_dir = out_dir / 'queue'
    crashes_dir = out_dir / 'crashes'
    
    queue = []
    if queue_dir.exists():
        queue = [f.name for f in queue_dir.iterdir() if f.is_file() and not f.name.startswith('.')]
        
    crashes = []
    if crashes_dir.exists():
        crashes = [f.name for f in crashes_dir.iterdir() if f.is_file() and not f.name.startswith('.')]
        
    return jsonify({
        'queue': sorted(queue),
        'crashes': sorted(crashes),
        'index': {
            'by_endpoint': state['corpus_index']['by_endpoint'],
            'counts': state['corpus_index']['counts']
        },
        'files': state['corpus_index']['files']
    })

@app.route('/api/corpus/<type>/<filename>')
def get_corpus_file(type, filename):
    if not state['harness_dir']:
        return jsonify({'error': 'No harness'}), 404
        
    campaign_dir = state['harness_dir'] / 'campaign'
    revisions_dir = campaign_dir / 'revisions'
    latest_rev = sorted([x for x in revisions_dir.iterdir() if x.is_dir() and x.name.isdigit()], key=lambda x: int(x.name))[-1]
    out_dir = latest_rev / 'outputs'
    
    if type == 'queue':
        file_path = out_dir / 'queue' / filename
    elif type == 'crashes':
        file_path = out_dir / 'crashes' / filename
    else:
        return jsonify({'error': 'Invalid type'}), 400
        
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
        
    try:
        with open(file_path, 'r') as f:
            # It's JSON serialized GraphInput
            content = json.load(f)
            return jsonify(content)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def do_monitor(args):
    project_path = Path(args.path).absolute()
    if not project_path.exists():
        print(f"Project path {project_path} does not exist")
        return

    state['project_path'] = project_path

    # Find harness
    harnesses_dir = project_path / 'harnesses'
    if not harnesses_dir.exists():
        print(f"No harnesses directory found at {harnesses_dir}")
        return

    if args.name:
        harness_dir = harnesses_dir / args.name
    else:
        # Pick first
        harnesses = [x for x in harnesses_dir.iterdir() if x.is_dir()]
        if not harnesses:
            print("No harnesses found")
            return
        harness_dir = harnesses[0]
    
    state['harness_dir'] = harness_dir
    state['harness_name'] = harness_dir.name
    
    print(f"Monitoring harness: {harness_dir.name}")
    print(f"Starting web server on http://localhost:{args.port}")

    # Start log reader thread
    t = threading.Thread(target=log_reader_thread, daemon=True)
    t.start()
    
    # Start corpus indexer thread
    t2 = threading.Thread(target=corpus_indexer_thread, daemon=True)
    t2.start()

    app.run(host='0.0.0.0', port=args.port, debug=False)

def register(subparsers):
    subparser = subparsers.add_parser('monitor', help='Launch the visualization dashboard')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-n','--name', help='The name of the harness', default=None)
    subparser.add_argument('--port', type=int, help='Port to run the server on', default=5000)
    subparser.set_defaults(func=do_monitor)
