
from pathlib import Path
from typing import List, Optional, Any
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
import subprocess
import tempfile
from ..utils.colors import Colors
from ..data.project_info import ProjectInfo, BuildConfig, CodegenConfig, CodegenHeader


MAX_FILES = 20


class AutogenConfig(BaseModel):
    # Full path to git repo
    repo: str
    # Name of the project
    name: str
    # Cloned project source code path
    project_src: str
    # Model to use for LLM
    model: str


PROMPT_SELECT_FILES = """
You are an expert software engineer.

Your task is to build a C/C++ library along with a simple test program.

You will be provided with a list of files in the project.
Your task is to select the most relevant files needed to understand how to build the project.
You can select at most {MAX_FILES} files, follow the guidelines below:

[GUIDELINES]
- Select files that are needed to understand the build system, any dependencies, and enough information to write a Dockerfile and test program
- For build systems, README.md/INSTALL.md/CMakeLists.txt/Makefile etc... may be useful
- For the test program, consider finding a simple example program or test
[/GUIDELINES]

[FILES]
{files}
[/FILES]
"""


class SelectFilesResult(BaseModel):
    files: List[str]


def select_files(project_dir: str, model: str) -> List[str]:
    print(f'[{Colors.GREEN}+{Colors.END}] Selecting files...')
    project_dir = Path(project_dir)
    files = list(project_dir.glob('**/*'))
    files = [f for f in files if f.is_file()]
    files = [f.relative_to(project_dir) for f in files]
    files = [str(f) for f in files]
    # Filter out .git files
    files = [f for f in files if not f.startswith('.git')]

    
    prompt = PROMPT_SELECT_FILES.format(files='\n'.join(files), MAX_FILES=MAX_FILES)
    llm = ChatOpenAI(model=model).with_structured_output(SelectFilesResult)
    res = llm.invoke(prompt)

    out_files = res.files

    valid_files = []
    # Ensure files exist
    for file in out_files:
        if not (project_dir / file).exists():
            print(f'[{Colors.YELLOW}!{Colors.END}] File {file} does not exist')
        else:
            valid_files.append(file)

    if len(valid_files) > MAX_FILES:
        print(f'[{Colors.YELLOW}!{Colors.END}] Selected {len(valid_files)} files, expected at most {MAX_FILES}')
        valid_files = valid_files[:MAX_FILES]

    print(f'[{Colors.GREEN}+{Colors.END}] Selected {len(valid_files)} files')
    for file in valid_files:
        print(f'[{Colors.CYAN}*{Colors.END}] {file}')

    return valid_files


PROMPT_BUILD = """
You are an expert software engineer.

Your task is to write a Dockerfile that can build a project along with a simple test program.
You will be provided with relevant files to understand the build system, any dependencies, and a simple test program.
Follow the guidelines below:

[GUIDELINES]
You will be provided with several files containing relevant information.
You will need to produce three things:
- A Dockerfile that can build the project (see [DOCKERFILE])
- A simple test.cpp program that acts as a hello world program (see [TEST])
[/GUIDELINES]

[DOCKERFILE]
Your Dockerfile must start with the following line:
```
ARG BASE_IMAGE
FROM ${{BASE_IMAGE}}
```
The image provided will be an Ubuntu 22.04 image with a minimal set of packages already installed.
The source code for the project you are building is already located in `/fuzz/src`, do not clone the repo again.

Your Dockerfile must do the following:
1. Install any extra dependencies needed to build the project
    - Make sure to install every dependency you might need to build the project (including the development version)
    - Also install any required build tools (e.g. cmake, make, etc...)
    - Be extra careful, its ok to install more dependencies than needed, but dont miss any
2. Configure the project with the following requirements:
    - The C compiler must be `stitch_libafl_cc`
    - The C++ compiler must be `stitch_libafl_cxx`
    - The install prefix must be `/fuzz/install`
    - If possible, build only a static library
3. Build the project
4. Install the project into `/fuzz/install`

Do not attempt to build the test program in the Dockerfile, that will be done in a later step.

If the project does not produce a library, compile source files into an object file and move them into `/fuzz/install/lib`.
[/DOCKERFILE]

[TEST]
Your test program should be a simple single-file program that uses the library.
It will be named `test.cpp` and will be located in the root of the project.

Keep the test program small and simple.
Ensure that you include all the necessary headers from the library.
Tip: if there are multiple header files that might be relevant, include all of them to be safe.

Specify the full path to the header files, usually something like:
#include "/fuzz/install/include/<header_file>.h" or #include "/fuzz/install/include/<mylib>/<header_file>.h"
[/TEST]

[FILES]
{files}
[/FILES]
"""

PROMPT_RETRY_EXTRA = """
[LAST_ATTEMPT]
Your last attempt produced the following Dockerfile and test program:

[DOCKERFILE]
{dockerfile}
[/DOCKERFILE]

[TEST]
{test_program}
[/TEST]

During compilation, there was an error:
[ERROR]
{error}
[/ERROR]

Try to fix the Dockerfile and test program to avoid the error.
[/LAST_ATTEMPT]
"""


class BuildResult(BaseModel):
    dockerfile: str = Field(description="The Dockerfile to build the project")
    test_program: str = Field(description="The test program to test the project")


def build(project_dir: str, name: str, selected_files: List[str], model: str, last_attempt: (str, str, dict) = None) -> BuildResult:
    print(f'[{Colors.GREEN}+{Colors.END}] Building...')
    project_dir = Path(project_dir)

    file_contents = ''
    for file in selected_files:
        with open(project_dir / file, 'r') as f:
            container_path = f'/fuzz/src/{file}'
            file_contents += f'\n<file path="{container_path}">\n'
            file_contents += f.read()
            file_contents += f'\n</file>\n'

    prompt = PROMPT_BUILD.format(files=file_contents)

    if last_attempt is not None:
        dockerfile, test_program, error = last_attempt
        prompt += PROMPT_RETRY_EXTRA.format(dockerfile=dockerfile, test_program=test_program, error=error)

    llm = ChatOpenAI(model=model).with_structured_output(BuildResult)
    res = llm.invoke(prompt)

    print(f'[{Colors.GREEN}+{Colors.END}] Build result:')
    print(f'[{Colors.CYAN}*{Colors.END}] Dockerfile:\n{res.dockerfile}')
    print(f'[{Colors.CYAN}*{Colors.END}] Test program:\n{res.test_program}')

    return res


def run_in_container(image_name: str, command: str) -> str:
    res = subprocess.run(['docker', 'run', '-it', image_name, 'bash', '-c', command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return res.stdout


def extract_flags(image_name: str) -> List[str]:
    # 1. Find new pkgconfig
    pkgconfig = run_in_container(image_name, 'ls /fuzz/install/lib/pkgconfig')
    pkgconfig = pkgconfig.split('\n')
    pkgconfig = [p.split('.')[0] for p in pkgconfig if p.endswith('.pc')]
    if len(pkgconfig) == 0:
        return []

    lib = pkgconfig[0]

    # 2. Extract the project info
    flags = run_in_container(image_name, f'PKG_CONFIG_PATH=/fuzz/install/lib/pkgconfig/ pkg-config --cflags --libs --static {lib}')
    flags = flags.split()
    return flags


def extract_headers(image_name: str) -> List[str]:
    headers = run_in_container(image_name, 'find /fuzz/install/include -type f -name "*.h"')
    headers = headers.split('\n')
    headers = [x.strip() for x in headers if x.strip() != '']
    headers = [CodegenHeader(name=h, language='c') for h in headers]
    return headers


def validate_dockerfile(config: AutogenConfig, dockerfile: str, test_program: str) -> dict:
    print(f'[{Colors.GREEN}+{Colors.END}] Validating Dockerfile...')

    # Build the base image
    base_image = ''
    base_image += 'FROM gfafl_base\n'
    base_image += f'RUN git clone {config.repo} /fuzz/src\n'
    base_image += 'WORKDIR /fuzz/src\n'
    base_image += 'ENV DEBIAN_FRONTEND=noninteractive\n'
    base_image += 'ENV LD_LIBRARY_PATH=/fuzz/install/lib\n'

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        base_image_path = temp_dir / 'base_image.dockerfile'
        with open(base_image_path, 'w') as f:
            f.write(base_image)

        # Base image is written by us, this should never fail
        subprocess.run([
            'docker', 'build',
            '--platform', 'linux/amd64',
            '-t', f'base_{config.name}',
            '-f', base_image_path,
            str(temp_dir)
        ])

        # Try to build the provided dockerfile
        dockerfile_path = temp_dir / 'main.dockerfile'
        with open(dockerfile_path, 'w') as f:
            f.write(dockerfile)

        res = subprocess.run([
            'docker', 'build',
            '--platform', 'linux/amd64',
            '--build-arg', f'BASE_IMAGE=base_{config.name}',
            '-t', f'main_{config.name}',
            '-f', dockerfile_path,
            str(temp_dir)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f'[{Colors.RED}-{Colors.END}] Dockerfile build failed with error: {res.stderr}')
            return {
                'success': False,
                'scope': 'dockerfile',
                'error': f'Dockerfile build failed with error code {res.returncode}',
                'error_output': res.stderr
            }
        
        print(f'[{Colors.GREEN}+{Colors.END}] Dockerfile build succeeded')
        
        flags = extract_flags(f'main_{config.name}')
        print(f'[{Colors.GREEN}+{Colors.END}] Extracted flags: {flags}')
        headers = extract_headers(f'main_{config.name}')
        print(f'[{Colors.GREEN}+{Colors.END}] Extracted headers: {headers}')

        info = ProjectInfo(
            name=config.name,
            codebase='/fuzz/src',
            build=BuildConfig(
                source_files=[],
                include_paths=[],
                lib_paths=[],
                libs=[],
                flags=flags
            ),
            codegen=CodegenConfig(
                headers=headers
            )
        )

        info_path = temp_dir / 'info.json'
        with open(info_path, 'w') as f:
            f.write(info.model_dump_json(indent=4))

        test_path = temp_dir / 'test.cpp'
        with open(test_path, 'w') as f:
            f.write(test_program)

        # Create the builder dockerfile
        builder = ''
        builder += f'FROM main_{config.name}\n'
        builder += 'COPY info.json /fuzz/info.json\n'
        builder += 'COPY test.cpp /fuzz/test.cpp\n'
        builder += 'ENV ASAN_OPTIONS=detect_leaks=0\n'
        builder += 'RUN agfi build external --info /fuzz/info.json /fuzz/test && /fuzz/test\n'

        builder_path = temp_dir / 'builder.dockerfile'
        with open(builder_path, 'w') as f:
            f.write(builder)

        res = subprocess.run(
            [
                'docker', 'build',
                '--platform', 'linux/amd64',
                '-t', f'builder_{config.name}',
                '-f', builder_path,
                str(temp_dir)
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            print(f'[{Colors.RED}-{Colors.END}] Builder failed with error: {res.stderr}')
            return {
                'success': False,
                'scope': 'compile',
                'error': f'Builder failed with error code {res.returncode}',
                'error_output': res.stderr
            }
        
        print(f'[{Colors.GREEN}+{Colors.END}] Builder build succeeded')
        
        return {
            'success': True,
            'info': info,
            'dockerfile': dockerfile,
            'test_program': test_program,
            'base_image': base_image
        }
    

def validate_headers(config: AutogenConfig, info: ProjectInfo) -> ProjectInfo:
    print(f'[{Colors.GREEN}+{Colors.END}] Validating headers...')

    to_use = [x for x in info.codegen.headers]
    selected = []

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir = Path(temp_dir)
        info_path = temp_dir / 'info.json'

        while len(to_use) > 0:
            curr = to_use.pop(0)
            for insertion_idx in range(len(selected) + 1):
                headers = selected[:insertion_idx] + [curr] + selected[insertion_idx:]
                info = ProjectInfo(
                    name=config.name,
                    codebase='/fuzz/src',
                    build=info.build,
                    codegen=CodegenConfig(headers=headers)
                )
                info_path.write_text(info.model_dump_json(indent=4))

                # Run a validation command to see if the headers are valid
                res = subprocess.run([
                    'docker', 'run',
                    '-it',
                    '--rm',
                    '-v', f'{str(info_path)}:/fuzz/workspace/info.json',
                    f'main_{config.name}',
                    'bash', '-c', f'cd /fuzz/workspace && agfi build test-obj int'
                ])
                if res.returncode == 0:
                    print(f'[{Colors.GREEN}+{Colors.END}] Ordering headers: {headers}')
                    selected = headers
                    break
            else:
                print(f'[{Colors.RED}-{Colors.END}] Invalid header: {curr}')

    return ProjectInfo(
        name=config.name,
        codebase='/fuzz/src',
        build=info.build,
        codegen=CodegenConfig(headers=selected)
    )


def cleanup_containers(config: AutogenConfig) -> None:
    subprocess.run(['docker', 'rmi', f'base_{config.name}'])
    subprocess.run(['docker', 'rmi', f'main_{config.name}'])
    subprocess.run(['docker', 'rmi', f'builder_{config.name}'])


class AutogenResult(BaseModel):
    base_image: str
    dockerfile: str
    test_program: str
    info: ProjectInfo


def run_autogen(config: AutogenConfig) -> Optional[AutogenResult]:
    project_src = config.project_src
    model = config.model

    selected_files = select_files(project_src, model)
    build_result = build(project_src, config.name, selected_files, model)

    dockerfile = build_result.dockerfile
    test_program = build_result.test_program

    # Validate the configuration
    res = validate_dockerfile(config, dockerfile, test_program)

    tries = 0
    while tries < 3 and not res['success']:
        # Retry with the last error case
        build_result = build(project_src, config.name, selected_files, model, (dockerfile, test_program, res))
        dockerfile = build_result.dockerfile
        test_program = build_result.test_program
        res = validate_dockerfile(config, dockerfile, test_program)
        tries += 1

    if res['success']:
        res['info'] = validate_headers(config, res['info'])

    # Cleanup
    cleanup_containers(config)

    if res['success']:
        return AutogenResult(
            dockerfile=dockerfile,
            base_image=res['base_image'],
            test_program=test_program,
            info=res['info']
        )
    else:
        print(f'[{Colors.RED}-{Colors.END}] Autogen failed: {res["error"]}')
        print(f'[{Colors.RED}-{Colors.END}] Error output: {res["error_output"]}')
        return None
