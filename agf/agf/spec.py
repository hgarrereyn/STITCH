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

class Object(BaseModel):
    name: str
    canonical_type: str

class Objects(BaseModel):
    objects: List[Object]

    def find(self, name: str) -> Object:
        return next((o for o in self.objects if o.name == name), None)

class ObjectArg(BaseModel):
    name: str
    typ: str
    dir: str

class Functions(BaseModel):
    functions: List[str]

class Stub(BaseModel):
    object_args: List[ObjectArg]

    def to_endpoint(self, name: str, template: str, objects: Objects):
        variables = [Variable(name='$' + o.name, dir=o.dir, typ=objects.find(o.typ).canonical_type) for o in self.object_args]

        # replace template variables names with $name
        for v in variables:
            template = re.sub(rf'\b{re.escape(v.name[1:])}\b', v.name, template)

        return Endpoint(name=name, variables=variables, template=template)

class Stubs(BaseModel):
    stubs: List[Stub]

    def fixup(self, objects: Objects):
        types = [o.name for o in objects.objects]
        for stub in self.stubs:
            args = [o for o in stub.object_args if o.typ in types]
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

class TemplateError(BaseModel):
    error: str
