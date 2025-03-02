import re

from pydantic import BaseModel
from typing import List

from .colors import Colors


class Variable(BaseModel):
    name: str
    dir: str
    typ: str


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

# --------------- ARCH v2 ---------------

class Objects(BaseModel):
    objects: List[str]

class ObjectArg(BaseModel):
    name: str
    typ: str
    dir: str

class Functions(BaseModel):
    functions: List[str]

class Stub(BaseModel):
    object_args: List[ObjectArg]

    def to_endpoint(self, name: str, template: str):
        variables = [Variable(name='$' + o.name, dir=o.dir, typ=o.typ) for o in self.object_args]
        variables.append(Variable(name='$__seed', dir='*', typ='unsigned int'))
        
        # replace all instances of __<name> with $<name> for name in object_args
        for o in self.object_args:
            # use re.sub matching word boundaries
            template = re.sub(r'\b__' + re.escape(o.name) + r'\b', '$' + o.name, template)
        
        template = f'gf_rand::seed($__seed);\n' + template

        return Endpoint(name=name, variables=variables, template=template)

class DetailedObjectArg(BaseModel):
    name: str
    type: str
    base_type: str
    dir: str
    is_optional: bool

    def to_object_arg(self):
        return ObjectArg(name=self.name, typ=self.base_type, dir=self.dir)

class DetailedStub(BaseModel):
    object_args: List[DetailedObjectArg]

    def fixup(self, objects: Objects):
        self.object_args = [o for o in self.object_args if o.base_type in objects.objects]

    def expand_stubs(self) -> "Stubs":
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
            stubs.append(Stub(object_args=[o.to_object_arg() for o in current_args]))

        return Stubs(stubs=stubs)

class Stubs(BaseModel):
    stubs: List[Stub]

    def fixup(self, objects: Objects):
        for stub in self.stubs:
            args = [o for o in stub.object_args if o.typ in objects.objects]
            stub.object_args = args

    def dedup(self):
        # keep only unique stubs, only the type and direction of the arguments matter
        seen = set()
        stubs = []
        for stub in self.stubs:
            if tuple([(o.typ, o.dir) for o in stub.object_args]) not in seen:
                seen.add(tuple([(o.typ, o.dir) for o in stub.object_args]))
                stubs.append(stub)
        self.stubs = stubs

class Templates(BaseModel):
    templates: List[str]

class Template(BaseModel):
    template: str

class DetailedTemplate(BaseModel):
    function_name: str
    function_args: List[str]
    function_body: str

class TemplateError(BaseModel):
    error: str
