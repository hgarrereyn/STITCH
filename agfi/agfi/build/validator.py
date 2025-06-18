from typing import Optional
import tempfile

from ..data.project_info import ProjectInfo
from .buildcmd import build_command


def format_and_check(main_code: str, info: ProjectInfo, verbose: bool = False, external: bool = False) -> Optional[str]:
    code = ''
    code += '#include <cstdio>\n'
    code += '#include <cstdlib>\n'
    code += '#include <cstdint>\n'
    code += '#include <cstring>\n'

    if info.codegen.defines:
        for define in info.codegen.defines:
            code += '#define %s %s\n' % (define.name, define.value)

    for header in info.codegen.headers:
        if header.language == 'c':
            code += 'extern "C" {\n'
            code += '#include "%s"\n' % header.name
            code += '}\n'
        elif header.language == 'cpp':
            code += '#include "%s"\n' % header.name

    if external:
        code += '#include "gf_util.h"\n'

    code += main_code

    with tempfile.TemporaryDirectory() as tmpdir:
        f_code = '%s/code.cpp' % tmpdir
        with open(f_code, 'w') as f:
            f.write(code)

        path = '%s/code' % tmpdir
        res = build_command(path, info, external=True, verbose=verbose)
        if res.returncode != 0:
            return res.stderr
        else:
            return None


def validate_object(object: str, info: ProjectInfo, verbose: bool = False) -> Optional[str]:
    """Check that the object symbol exists and can be the type of a variable."""
    code = f'''
int main() {{
    {object} x;
    x = x; // Ensure x is treated as an identifier
    return 0;
}}
'''

    return format_and_check(code, info, verbose)


def validate_function(function: str, info: ProjectInfo, verbose: bool = False) -> Optional[str]:
    """Check that the function symbol exists and can be linked."""
    code = f'''
int main() {{
    printf("f: %p", {function});
    return 0;
}}
'''

    return format_and_check(code, info, verbose)
