from typing import Optional
import subprocess
import json
import tempfile


def validate_object(object: str, build_ext_script: str, config: str) -> Optional[str]:
    # Check that the object symbol exists and can be the type of a variable
    code = ''
    config = json.loads(open(config).read())

    code = ''
    code += '#include <cstdio>\n'
    code += '#include <cstdlib>\n'
    code += '#include <cstdint>\n'
    code += '#include <cstring>\n'

    for header in config['headers']:
        if 'CHeader' in header:
            code += 'extern "C" {\n'
            code += '#include "%s"\n' % header['CHeader']
            code += '}\n'
        elif 'CppHeader' in header:
            code += '#include "%s"\n' % header['CppHeader']

    code += f'''
int main() {{
    {object} x;
    x = x; // Ensure x is treated as an identifier
    return 0;
}}
'''

    with tempfile.TemporaryDirectory() as tmpdir:
        f_code = '%s/code.cpp' % tmpdir
        with open(f_code, 'w') as f:
            f.write(code)

        path = '%s/code' % tmpdir

        res = subprocess.run([build_ext_script, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            return res.stderr.decode()
        else:
            return None


def validate_function(function: str, build_ext_script: str, config: str) -> Optional[str]:
    # Check that the function symbol exists
    code = ''
    config = json.loads(open(config).read())

    code = ''
    code += '#include <cstdio>\n'
    code += '#include <cstdlib>\n'
    code += '#include <cstdint>\n'
    code += '#include <cstring>\n'

    for header in config['headers']:
        if 'CHeader' in header:
            code += 'extern "C" {\n'
            code += '#include "%s"\n' % header['CHeader']
            code += '}\n'
        elif 'CppHeader' in header:
            code += '#include "%s"\n' % header['CppHeader']

    code += f'''
int main() {{
    void *f = (void *){function};
    return 0;
}}
'''

    with tempfile.TemporaryDirectory() as tmpdir:
        f_code = '%s/code.cpp' % tmpdir
        with open(f_code, 'w') as f:
            f.write(code)

        path = '%s/code' % tmpdir

        res = subprocess.run([build_ext_script, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            return res.stderr.decode()
        else:
            return None
