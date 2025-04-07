import re

from pydantic import BaseModel
from typing import List, Optional

from .colors import Colors


class Variable(BaseModel):
    name: str
    dir: str
    typ: str


# Represents an endpoint as represented in the schema
class Endpoint(BaseModel):
    name: str
    variables: List[Variable]
    template: str

    def pprint(self):
        print(f'{Colors.BOLD}{self.name}{Colors.END}')
        for v in self.variables:
            print(f'{Colors.LIGHT_BLUE}{v.name}{Colors.END} {Colors.LIGHT_GREEN}[{v.dir}]{Colors.END} {v.typ}')
        print(f'{Colors.BOLD}Template{Colors.END}')
        print(pprint_template(self.template))
        print()

    def to_function(self):
        stub = EndpointStub(endpoint_name=self.name, function_name=self.name, variables=self.variables)

        code = ''
        code += 'void test_function('
        code += stub.mock_function_sig()
        code += '){\n'
        code += self.template
        code += '\n}\n'
        return code


class Schema(BaseModel):
    endpoints: List[Endpoint]

    def pprint(self):
        for e in self.endpoints:
            e.pprint()


def pprint_template(template: str):
    # replace $name with colors
    template = re.sub(r'\$(\w+)', f'{Colors.LIGHT_BLUE}$\\1{Colors.END}', template)
    return template


class Error(BaseModel):
    endpoint: Endpoint
    err_type: str
    error: str


class Errors(BaseModel):
    errors: List[Error]

    def pprint(self):
        for e in self.errors:
            print(f'{Colors.RED}{e.err_type}{Colors.END}: {e.error}')
            e.endpoint.pprint()
            print()


class EndpointStub(BaseModel):
    function_name: str
    endpoint_name: str
    variables: List[Variable]

    def mock_function_sig(self) -> str:
        """Generate a mock function signature for the endpoint (used in prompting)"""
        sig_lines = []

        inputs = [o for o in self.variables if o.dir != 'new']
        outputs = [o for o in self.variables if o.dir == 'new']

        for o in inputs:
            if o.dir == 'del':
                sig_lines.append(f'    {o.typ} {o.name} /* input (delete) */')
            else:
                sig_lines.append(f'    {o.typ} {o.name} /* input (must use) */')

        for o in outputs:
            sig_lines.append(f'    {o.typ}& {o.name} /* output (must initialize) */')

        sig = '\n'
        for i, line in enumerate(sig_lines):
            sig += line
            if i < len(sig_lines) - 1:
                sig += ',\n'
        sig += '\n'

        return sig

    def mock_function_args(self) -> List[str]:
        """Generate mock function args for the endpoint (used in prompting)"""
        function_args = []

        inputs = [o for o in self.variables if o.dir != 'new']
        outputs = [o for o in self.variables if o.dir == 'new']

        if len(inputs) > 0:
            function_args += [f"{o.typ} {o.name}" for o in inputs]

        if len(outputs) > 0:
            function_args += [f"{o.typ}& {o.name}" for o in outputs]

        return function_args
    
    def to_endpoint(self, template: str):
        variables = [Variable(name=o.name, dir=o.dir, typ=o.typ) for o in self.variables]
        return Endpoint(name=self.endpoint_name, variables=variables, template=template)


# Represents the result of a function completion task for generating an endpoint template
# Includes some extra information to help with cross-validation
class EndpointFunctionCompletion(BaseModel):
    function_name: str
    function_args: List[str]
    function_body: str

    # Validate the completion against the original stub
    def validate(self, stub: EndpointStub) -> Optional[str]:
        # Ensure there is no function signature in the body
        if re.match(r'^\s*void\s+\w+\s*\(', self.function_body):
            return 'Do not include the function signature in the function body'
        
        # Ensure the function args match the expected args
        # if self.function_args != stub.mock_function_args():
        #     return f'Function args do not match expected args {self.function_args} != {stub.mock_function_args()}'

        # Ensure there is no #include or #define in the function body
        if re.search(r'\b#include\b', self.function_body) or re.search(r'\b#define\b', self.function_body):
            return 'Do not include any #include or #define in the function body'

        # Search for "return;" or "return <x>;"
        if re.search(r'\breturn\s*;', self.function_body) or re.search(r'\breturn\s+\w+\s*;', self.function_body):
            return 'Do not use the return statement, use FUZZ_BAIL() instead'
        
        # Search for "throw <x>;"
        if re.search(r'\bthrow\s+\w+\s*;', self.function_body):
            return 'Do not throw any exceptions in the function body'

        return None


# Represents a variable, modeled in a way that is easier for the LLM to understand
class DetailedObjectArg(BaseModel):
    name: str
    type: str
    base_type: str
    dir: str
    is_optional: bool


class DetailedStub(BaseModel):
    object_args: List[DetailedObjectArg]

    def validate(self, objects: List[str]) -> Optional[str]:
        err_msg = ''

        for o in self.object_args:
            if o.dir not in ['del', 'new', '*']:
                err_msg += f'{o.base_type} has invalid direction {o.dir} (must be del, new, or *) \n'

        keep = []
        for o in self.object_args:
            if o.base_type in objects:
                keep.append(o)
            elif o.base_type == 'none':
                continue
            else:
                err_msg += f'{o.base_type} is not in the list of objects {objects}\n'

        self.object_args = keep
        if len(err_msg) > 0:
            return err_msg
        else:
            return None

    def expand_stubs(self, function: str) -> List[EndpointStub]:
        # Generate all combinations of optional arguments being present/absent
        optional_indices = [i for i, arg in enumerate(self.object_args) if arg.is_optional]
        stubs = []

        # Generate all possible combinations using binary counting
        num_combinations = 1 << len(optional_indices)  # 2^n combinations
        for i in range(num_combinations):
            # Create a new list of args for this combination
            current_args = []
            for j, arg in enumerate(self.object_args):
                # If argument is not optional, always include it
                if not arg.is_optional:
                    current_args.append(arg)
                # If argument is optional, include it only if its bit is set
                elif j in optional_indices:
                    bit_position = optional_indices.index(j)
                    if (i & (1 << bit_position)):
                        current_args.append(arg)
            
            # Create a new stub with this combination of arguments
            stubs.append(EndpointStub(
                function_name=function,
                endpoint_name=f'{function}_v{i}',
                variables=[Variable(name='__' + sanitized_argument_name(o.name), dir=o.dir, typ=o.base_type) for o in current_args]
            ))

        return stubs


def sanitized_argument_name(name: str) -> str:
    """Sanitize an argument name to be used in a function signature"""
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)
