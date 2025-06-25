import subprocess
import tempfile
from ..data.project_info import ProjectInfo


def build_full(path: str, info: ProjectInfo):
    res = synth_command(path, info)
    if res.returncode != 0:
        raise Exception(res.stderr)

    res = build_command(path, info, False)
    if res.returncode != 0:
        raise Exception(res.stderr)

def synth_command(path: str, info: ProjectInfo) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_info = tmpdir + '/info.json'
        with open(tmp_info, 'w') as f:
            f.write(info.model_dump_json(indent=4))

        res = subprocess.run([
            'gf_synth',
            str(path) + '.json',
            '--info', str(tmp_info),
            '-o', str(path) + '.cpp',
            '-p', str(path) + '.pspec'
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res

def build_command(path: str, info: ProjectInfo, external: bool, verbose: bool = False) -> subprocess.CompletedProcess:
    cmd = [
        'gf_libafl_cxx',
        '-g',
        '-std=c++17',
    ]
    if not external:
        cmd.append('--libafl')
    cmd += [
        '-o', str(path),
        str(path) + '.cpp',
    ]

    for source in info.build.source_files:
        cmd.append(source)
    for include in info.build.include_paths:
        cmd.append('-I' + include)
    for lib_path in info.build.lib_paths:
        cmd.append('-L' + lib_path)
    for lib in info.build.libs:
        cmd.append('-l' + lib)
    if info.build.flags:
        for flag in info.build.flags:
            cmd.append(flag)

    if external:
        cmd.append('-I/src/gf_core/external/')

    if verbose:
        print('[BUILD] ' + ' '.join(cmd))

    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if verbose:
        if res.stdout != '':
            print('[BUILD OUT] ' + res.stdout)
        if res.stderr != '':
            print('[BUILD ERR] ' + res.stderr)
    return res
