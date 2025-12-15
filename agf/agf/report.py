from pathlib import Path
import shutil
import tempfile
import subprocess
import json
import sys

from .utils.colors import Colors
from .utils.project import Project
from .data.project_info import ProjectInfo


def do_report(args):
    path = Path(args.path)
    project = Project(path)
    if not project.exists() or not project.has_config():
        print(f'[{Colors.RED}-{Colors.END}] Could not find project at {path.absolute()}')
        return

    info = ProjectInfo.model_validate_json(open(project.path / 'config' / 'info.json').read())

    if args.testcase is None:
        print(f'[{Colors.RED}-{Colors.END}] Testcase is required')
        return

    testcase_path = Path(args.testcase)
    testcase = testcase_path.read_text()

    # Find crash report
    crash_type = 'unknown'
    crash_type_reasoning = 'unknown'
    explanation = 'unknown'

    crash_assessment_path = Path(args.testcase.replace('.cpp', '_assessment.json'))
    if crash_assessment_path.exists():
        crash_assessment = json.loads(open(crash_assessment_path).read())
        crash_type = crash_assessment['crash_type']
        crash_type_reasoning = crash_assessment['crash_type_reasoning']
        explanation = crash_assessment['explanation']

    custom_dockerfile = Path(args.custom_dockerfile).read_text() if args.custom_dockerfile is not None else None
    base, tag = project.build_external_docker(override_commit=args.commit, testcase=testcase, verbose=args.verbose, custom_dockerfile=custom_dockerfile)

    # Run the docker container
    cmd = f'''clang++-17 -fsanitize=address -g -O0 -o /fuzz/test /fuzz/testcase.cpp {" ".join(info.build_flags)}'''
    print(f'[{Colors.GREEN}+{Colors.END}] Running command: {cmd}')

    # `agf report` is used both interactively and programmatically (e.g. from a web UI).
    # `docker run -it` fails when there is no TTY; only request a TTY when we have one.
    has_tty = sys.stdin.isatty() and sys.stdout.isatty()
    docker_tty_flags = ['-it'] if has_tty else []

    res = subprocess.run([
        'docker', 'run',
        '--platform', 'linux/amd64',
        *docker_tty_flags, '--rm',
        '-v', f'{str(testcase_path.absolute())}:/fuzz/testcase.cpp',
        tag,
        'bash', '-c', cmd
    ])
    if res.returncode != 0:
        print(f'[{Colors.RED}-{Colors.END}] Command failed with error code {res.returncode}')
        return

    print(f'[{Colors.GREEN}+{Colors.END}] Command succeeded')

    print(f'[{Colors.GREEN}+{Colors.END}] Building and running the testcase...')
    cmd += ' && /fuzz/test'
    print(f'[{Colors.GREEN}+{Colors.END}] Running command: {cmd}')
    res = subprocess.run([
        'docker', 'run',
        '--platform', 'linux/amd64',
        *docker_tty_flags, '--rm',
        '-v', f'{str(testcase_path.absolute())}:/fuzz/testcase.cpp',
        tag,
        'bash', '-c', cmd
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    print(f'[{Colors.GREEN}+{Colors.END}] Command output:\n{res.stdout.decode("utf-8")}')
    print(f'[{Colors.GREEN}+{Colors.END}] Command error:\n{res.stderr.decode("utf-8")}')

    # --------------- Report Generation ---------------

    template = f'''
Hi, there is a potential bug in (component) reachable by (method).

This bug was reproduced on {{commit_path}}.

Severity: {{crash_type}}

{{crash_type_reasoning}}

### Description

{{explanation}}

### POC

The following testcase demonstrates the bug:

**testcase.cpp**
```cpp
{{testcase}}
```

**stdout**
```text
{{crash_stdout}}
```

**stderr**
```text
{{crash_stderr}}
```

<details><summary>Steps to Reproduce</summary>
<p>

The crash was triaged with the following Dockerfile:

**Dockerfile**
```dockerfile
{{dockerfile}}
```

**Build Command**
```bash
{{build_cmd}}
```

**Reproduce**
1. Copy `Dockerfile` and `testcase.cpp` into a local folder.
2. Build the repro image:
```bash
docker build . -t repro --platform=linux/amd64
```
3. Compile and run the testcase in the image:
```bash
docker run \\
    -it --rm \\
    --platform linux/amd64 \\
    --mount type=bind,source="$(pwd)/testcase.cpp",target=/fuzz/testcase.cpp \\
    repro \\
    bash -c "{{escaped_build_cmd}}"
```

</p>
</details> 

---

### Additional Info
This testcase was discovered by `STITCH`, an autonomous fuzzing system. All reports are reviewed manually (by a human) before submission.
    '''

    config = project.get_config()
    commit = config.commit if args.commit is None else args.commit
    commit_path = f'{config.repo}/commit/{commit}'

    report = template.format(
        commit_path=commit_path,
        crash_type=crash_type,
        crash_type_reasoning=crash_type_reasoning,
        explanation=explanation,
        testcase=testcase,
        crash_stdout=res.stdout.decode("utf-8"),
        crash_stderr=res.stderr.decode("utf-8"),
        dockerfile=base,
        build_cmd=cmd,
        escaped_build_cmd=cmd.replace('"', '\\"')
    )

    print(f'[{Colors.GREEN}+{Colors.END}] Report:\n')
    print(report)


def register(subparsers):
    subparser = subparsers.add_parser('report', help='Generate fully external bug report')
    subparser.add_argument('path', nargs='?', help='The path to the project', default='.')
    subparser.add_argument('-t', '--testcase', type=str, help='The path to the testcase.cpp', default=None)
    subparser.add_argument('-c', '--commit', type=str, help='Test on a specific commit (will use pinned commit if not specified)', default=None)
    subparser.add_argument('-v', '--verbose', action='store_true', help='Verbose output', default=False)
    subparser.add_argument('--custom-dockerfile', type=str, help='Use a custom Dockerfile', default=None)
    subparser.set_defaults(func=do_report)
