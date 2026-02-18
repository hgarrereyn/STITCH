from typing import Optional, List
import tempfile
from pydantic import BaseModel, Field

from .data import FunctionImpl, FunctionImplError, ObjectInfo, FunctionInfo

from ..data.project_info import ProjectInfo
from ..build.validator import validate_object, validate_function, format_and_check
from ..build.build import build_full
from ..data.schema import Spec, Object


class ValidateStubResult(BaseModel):
    result: FunctionImpl | FunctionImplError = Field(description="The result of the validation")


def _find_valid_object_type(info: ProjectInfo, object: ObjectInfo) -> Optional[str]:
    """Find a valid object type for the given object."""
    if validate_object(object.ptr_type, info, False) is None:
        return object.ptr_type
    
    for alias in object.aliases:
        if validate_object(alias, info, False) is None:
            return alias

    return None


def run_validate_object(info: ProjectInfo, obj: ObjectInfo) -> List[ObjectInfo]:
    ptr_type = _find_valid_object_type(info, obj)
    
    if ptr_type is None:
        return []
    
    return [ObjectInfo(
        ptr_type=ptr_type,
        aliases=obj.aliases,
        description=obj.description,
        definition=obj.definition,
        direct_api_constructors=obj.direct_api_constructors,
        owned_by=obj.owned_by
    )]


def run_validate_function_name(info: ProjectInfo, name: str, verbose: bool = False) -> List[FunctionInfo]:
    if validate_function(name, info, verbose) is None:
        return [name]
    return []


def _test_compile_stub(info: ProjectInfo, impl: FunctionImpl, objects: List[ObjectInfo]) -> Optional[str]:
    code = ''
    code += 'int main() {}\n'

    args = []
    for inp in impl.inputs:
        args.append(f'{inp.type} {inp.name}')

    code += 'void test_stub('
    code += ', '.join(args)
    code += ') {\n'
    code += impl.code
    code += '\n}\n'
    
    return format_and_check(code, info, verbose=False, external=True)


def _test_stub_in_schema(info: ProjectInfo, impl: FunctionImpl, objects: List[ObjectInfo]) -> Optional[str]:
    schema = Spec(
        objects=[
            Object(name=o.ptr_type) for o in objects
        ] + [Object(name='void *')],
        endpoints=[impl.to_endpoint()]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        f_meta = '%s/schema.json' % tmpdir
        with open(f_meta, 'w') as f:
            f.write(schema.model_dump_json())
        
        try:
            build_full(f_meta[:-5], info)
        except Exception as e:
            return str(e)
        
    return None


def run_validate_stub(info: ProjectInfo, impl: FunctionImpl, objects: List[ObjectInfo]) -> List[ValidateStubResult]:
    res = None

    # Check that the inputs and outputs point to valid types
    valid_types = set([o.ptr_type for o in objects] + ['void *'])

    for inp in impl.inputs:
        if inp.type not in valid_types:
            return [ValidateStubResult(result=FunctionImplError(stub=impl, type='input', error=f'Input {inp.name} has type {inp.type} which is not a valid object type'))]
    
    for out in impl.outputs:
        if out.type not in valid_types:
            return [ValidateStubResult(result=FunctionImplError(stub=impl, type='output', error=f'Output {out.name} has type {out.type} which is not a valid object type'))]

    # Check that the function proto compiles properly (validates usage of macros)
    res = _test_compile_stub(info, impl, objects)
    if res is not None:
        return [ValidateStubResult(result=FunctionImplError(stub=impl, type='compile', error=res))]

    # Test that a full schema build works properly
    res = _test_stub_in_schema(info, impl, objects)
    if res is not None:
        return [ValidateStubResult(result=FunctionImplError(stub=impl, type='compile', error=res))]

    return [ValidateStubResult(result=impl)]
