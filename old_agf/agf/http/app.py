from flask import Flask, render_template, current_app, redirect, url_for
from pathlib import Path
from . import helpers
from datetime import datetime

app = Flask(__name__)

def time_ago(timestamp):
    """Convert a timestamp to a human-readable "time ago" string"""
    now = datetime.now()
    diff = now - timestamp
    
    seconds = diff.total_seconds()
    
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days > 1 else ''} ago"
    elif seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    else:
        months = int(seconds / 2592000)
        return f"{months} month{'s' if months > 1 else ''} ago"

# Register custom filters
app.jinja_env.filters['time_ago'] = time_ago

@app.route('/')
def index():
    workdir = Path(current_app.config['WORKDIR'])
    
    stages = helpers.get_stages(workdir)
    return render_template('stages.html', workdir=workdir, stages=stages)

@app.route('/stage/<int:stage>')
def stage(stage):
    workdir = Path(current_app.config['WORKDIR'])
    stage_details = helpers.get_stage_details(workdir, stage)
    return render_template('stage.html', workdir=workdir, stage=stage, stage_details=stage_details)

@app.route('/stage/<int:stage>/crash/<string:hash>')
def crash_details(stage, hash):
    workdir = Path(current_app.config['WORKDIR'])
    stage_details = helpers.get_stage_details(workdir, stage)
    
    # Find the crash bucket
    crash_bucket = None
    for bucket in stage_details.crash_buckets:
        if bucket.hsh == hash:
            crash_bucket = bucket
            break
    
    if crash_bucket is None:
        return redirect(url_for('stage', stage=stage))
    
    # Get first reproducer and minimized case if available
    stage_dir = workdir / f'stage_{stage}'
    bucket_dir = stage_dir / 'crash_buckets' / hash
    
    # Check for reproducers
    first_reproducer = None
    reproduced_path = bucket_dir / 'reproduced'
    if reproduced_path.exists():
        reproducer_files = list(reproduced_path.glob('reproducer_*.cpp'))
        if reproducer_files:
            reproducer_file = reproducer_files[0]
            first_reproducer = {
                'id': reproducer_file.stem,
                'code': reproducer_file.read_text()
            }
    
    # Check for minimized cases
    first_minimized = None
    minimized_path = bucket_dir / 'minimized'
    if minimized_path.exists():
        minimized_files = list(minimized_path.glob('minimized_*.cpp'))
        if minimized_files:
            minimized_file = minimized_files[0]
            first_minimized = {
                'id': minimized_file.stem,
                'code': minimized_file.read_text()
            }
    
    # Enhance crash instances with reproducer and minimized info
    enhanced_instances = []
    for instance in crash_bucket.instances:
        crash_id_num = instance.crash_id.split('_')[1] if '_' in instance.crash_id else ''
        
        # Check if this instance has a reproducer
        has_reproducer = False
        reproducer_file = bucket_dir / 'reproduced' / f'reproducer_{crash_id_num}.cpp'
        if reproducer_file.exists():
            has_reproducer = True
            
        # Check if this instance has been minimized
        has_minimized = False
        minimized_file = bucket_dir / 'minimized' / f'minimized_{crash_id_num}.cpp'
        if minimized_file.exists():
            has_minimized = True
            
        enhanced_instances.append({
            'crash_id': instance.crash_id,
            'crash_time': instance.crash_time,
            'has_reproducer': has_reproducer,
            'has_minimized': has_minimized
        })
    
    # Sort instances - put those with minimized or reproducer first
    enhanced_instances.sort(key=lambda x: (not (x['has_minimized'] or x['has_reproducer']), x['crash_time']), reverse=False)
    
    return render_template('crash_details.html', 
                          workdir=workdir, 
                          stage=stage, 
                          crash_bucket=crash_bucket,
                          first_reproducer=first_reproducer,
                          first_minimized=first_minimized,
                          instances=enhanced_instances)

@app.route('/stage/<int:stage>/crash/<string:hash>/<string:crash_id>')
def crash_instance(stage, hash, crash_id):
    workdir = Path(current_app.config['WORKDIR'])
    stage_dir = workdir / f'stage_{stage}'
    crash_dir = stage_dir / 'crash_buckets' / hash / 'crashes'
    
    # Get crash data
    crash_file = crash_dir / f'{crash_id}.json'
    graph_file = crash_dir / f'{crash_id}.graph'
    
    crash_data = crash_file.read_text() if crash_file.exists() else '{}'
    graph_data = graph_file.read_text() if graph_file.exists() else '{}'
    
    # Check if this crash has been reproduced or minimized
    bucket_dir = stage_dir / 'crash_buckets' / hash

    crash_id_num = crash_id.split('_')[1] if '_' in crash_id else ''
    
    # Get reproducer if available
    reproducer_code = None
    reproducer_file = bucket_dir / 'reproduced' / f'reproducer_{crash_id_num}.cpp'
    if reproducer_file.exists():
        reproducer_code = reproducer_file.read_text()
    
    # Get minimized code if available
    minimized_code = None
    minimized_file = bucket_dir / 'minimized' / f'minimized_{crash_id_num}.cpp'
    if minimized_file.exists():
        minimized_code = minimized_file.read_text()
    
    # Get crash report if available
    reproducer_report = None
    reproducer_report_file = bucket_dir / 'reproduced' / f'reproducer_{crash_id_num}.json'
    if reproducer_report_file.exists():
        reproducer_report = reproducer_report_file.read_text()

    minimized_report = None
    minimized_report_file = bucket_dir / 'minimized' / f'minimized_{crash_id_num}.json'
    if minimized_report_file.exists():
        minimized_report = minimized_report_file.read_text()
    
    return render_template('crash_instance.html', 
                          workdir=workdir, 
                          stage=stage, 
                          hash=hash,
                          crash_id=crash_id,
                          crash_data=crash_data,
                          graph_data=graph_data,
                          reproducer_code=reproducer_code,
                          minimized_code=minimized_code,
                          reproducer_report=reproducer_report,
                          minimized_report=minimized_report)

@app.route('/stage/<int:stage>/reproduced/<string:hash>')
def view_reproducer(stage, hash):
    workdir = Path(current_app.config['WORKDIR'])
    stage_dir = workdir / f'stage_{stage}'
    bucket_dir = stage_dir / 'crash_buckets' / hash
    
    # Get the first reproducer
    reproducer_code = "No reproducer available"
    reproducer_files = list((bucket_dir / 'reproduced').glob('reproducer_*.cpp'))
    if reproducer_files:
        reproducer_code = reproducer_files[0].read_text()
    
    return render_template('code_view.html',
                          title=f"Reproducer for {hash}",
                          code=reproducer_code,
                          stage=stage,
                          hash=hash,
                          back_url=url_for('stage', stage=stage))

@app.route('/stage/<int:stage>/minimized/<string:hash>')
def view_minimized(stage, hash):
    workdir = Path(current_app.config['WORKDIR'])
    stage_dir = workdir / f'stage_{stage}'
    bucket_dir = stage_dir / 'crash_buckets' / hash
    
    # Get the first minimized case
    minimized_code = "No minimized code available"
    minimized_files = list((bucket_dir / 'minimized').glob('minimized_*.cpp'))
    if minimized_files:
        minimized_code = minimized_files[0].read_text()
    
    return render_template('code_view.html',
                          title=f"Minimized case for {hash}",
                          code=minimized_code,
                          stage=stage,
                          hash=hash,
                          back_url=url_for('stage', stage=stage))

@app.route('/stage/<int:stage>/crash/<string:hash>/<string:crash_id>/reproducer')
def view_crash_reproducer(stage, hash, crash_id):
    workdir = Path(current_app.config['WORKDIR'])
    stage_dir = workdir / f'stage_{stage}'
    bucket_dir = stage_dir / 'crash_buckets' / hash
    
    # Get the reproducer for this specific crash
    crash_id_num = crash_id.split('_')[1] if '_' in crash_id else ''
    reproducer_code = "No reproducer available for this crash"
    reproducer_file = bucket_dir / 'reproduced' / f'reproducer_{crash_id_num}.cpp'
    
    if reproducer_file.exists():
        reproducer_code = reproducer_file.read_text()
    
    return render_template('code_view.html',
                          title=f"Reproducer for crash {crash_id}",
                          code=reproducer_code,
                          stage=stage,
                          hash=hash,
                          back_url=url_for('crash_instance', stage=stage, hash=hash, crash_id=crash_id))

@app.route('/stage/<int:stage>/crash/<string:hash>/<string:crash_id>/minimized')
def view_crash_minimized(stage, hash, crash_id):
    workdir = Path(current_app.config['WORKDIR'])
    stage_dir = workdir / f'stage_{stage}'
    bucket_dir = stage_dir / 'crash_buckets' / hash
    
    # Get the minimized code for this specific crash
    crash_id_num = crash_id.split('_')[1] if '_' in crash_id else ''
    minimized_code = "No minimized code available for this crash"
    minimized_file = bucket_dir / 'minimized' / f'minimized_{crash_id_num}.cpp'
    
    if minimized_file.exists():
        minimized_code = minimized_file.read_text()
    
    return render_template('code_view.html',
                          title=f"Minimized case for crash {crash_id}",
                          code=minimized_code,
                          stage=stage,
                          hash=hash,
                          back_url=url_for('crash_instance', stage=stage, hash=hash, crash_id=crash_id))

def run(args):
    app.config['WORKDIR'] = args.workdir
    app.run(debug=True, host='0.0.0.0', port=args.port)

def register(subparsers):
    parser = subparsers.add_parser('serve')
    parser.add_argument('--workdir', type=str, required=True)
    parser.add_argument('--port', type=int, default=3000)
    parser.set_defaults(func=run)
