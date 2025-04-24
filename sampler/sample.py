from z3 import *
from typing import List, Any, Tuple
from collections import namedtuple
from pydantic import BaseModel, Field
import time

from colors import Colors


class A(BaseModel):
    x: int
    refcount: int

class B(BaseModel):
    a: A

def init():
    pass

def new_a(x: int) -> A:
    return A(x)

def use_a(a: A):
    a.x += 1

def incref(a: A):
    a.refcount += 1

def decref(a: A):
    a.refcount -= 1

def del_a(a: A):
    assert a.refcount == 0
    pass

def sub_a(a: A) -> B:
    return B(a)

def del_b(b: B):
    pass


class Attr(BaseModel):
    name: str

    def instantiate(self, s: Solver, type_name: str, node_idx: int, object_idx: int):
        raise NotImplementedError
    
    def decode(self, model: ModelRef, attr: Any):
        raise NotImplementedError
    
    def const(self, v):
        raise NotImplementedError

    @staticmethod
    def eq(a, b):
        raise NotImplementedError


class AttrInt(Attr):
    def instantiate(self, s: Solver, type_name: str, node_idx: int, object_idx: int):
        return Int(f'attr_{type_name}_{self.name}_{node_idx}_{object_idx}')
    
    def decode(self, model: ModelRef, attr: Any):
        return eval(str(model.eval(attr, model_completion=True)))

    def const(self, v):
        return IntVal(v)

    @staticmethod
    def eq(a, b):
        return a == b


class AttrEnum(Attr):
    possible_values: List[str]

    def instantiate(self, s: Solver, type_name: str, node_idx: int, object_idx: int):
        values = [
            Bool(f'attr_{type_name}_{self.name}_{node_idx}_{object_idx}_{i}')
            for i in range(len(self.possible_values))
        ]
        ExactlyOne(s, values)
        return values
    
    def decode(self, model: ModelRef, attr: Any):
        v = [eval(str(model.eval(x, model_completion=True))) for x in attr]
        assert sum(v) == 1
        return self.possible_values[v.index(True)]

    def const(self, v):
        assert isinstance(v, str)
        assert v in self.possible_values
        return [BoolVal(v == x) for x in self.possible_values]

    @staticmethod
    def eq(a, b):
        return And([x == y for x, y in zip(a, b)])


class Effect(BaseModel):
    output_idx: int
    attr: str

class EffectSetField(Effect):
    value: Any

class EffectIncField(Effect):
    pass

class EffectDecField(Effect):
    pass


class Precondition(BaseModel):
    object_idx: int
    attr: str

class PreconditionAttrEq(Precondition):
    value: Any

class PreconditionAttrGte(Precondition):
    value: Any


class Constraint(BaseModel):
    pass

class ConstraintOutlives(Constraint):
    '''
    Object a must outlive object b
    '''
    object_idx_a: int
    object_idx_b: int


class Object(BaseModel):
    name: str
    attrs: List[Attr] = Field(default_factory=list)

class Func(BaseModel):
    name: str
    inputs: List[int] = Field(default_factory=list)
    outputs: List[int] = Field(default_factory=list)
    mappings: List[Tuple[int, int]] = Field(default_factory=list)

    # Requirement to execute
    preconditions: List[Precondition] = Field(default_factory=list)

    # Changes made to attributes on objects
    effects: List[Effect] = Field(default_factory=list)

    # Extra constraints on the graph, caused by the function
    constraints: List[Constraint] = Field(default_factory=list)


objects = [
    Object(name='A', attrs=[
        AttrInt(name='refcount')
    ]),
    Object(name='B', attrs=[
        AttrEnum(name='state', possible_values=['initialized', 'prepared', 'finalized'])
    ]),
]

funcs = []

for i in range(1):
    funcs.extend([
        Func(name=f'init_{i}', inputs=[], outputs=[0], effects=[
            EffectSetField(output_idx=0, attr='refcount', value=2)]
        ),
        Func(name=f'new_a_{i}', inputs=[], outputs=[0], effects=[EffectSetField(output_idx=0, attr='refcount', value=2)]),
        Func(name=f'use_a_{i}', inputs=[0], outputs=[0], mappings=[(0,0)]),
        Func(name=f'incref_{i}', inputs=[0], outputs=[0], mappings=[(0,0)], effects=[EffectIncField(output_idx=0, attr='refcount')]),
        Func(name=f'decref_{i}', inputs=[0], outputs=[0], mappings=[(0,0)], effects=[
            EffectDecField(output_idx=0, attr='refcount')
        ], preconditions=[
            PreconditionAttrGte(object_idx=0, attr='refcount', value=1)
        ]),
        Func(name=f'del_a_{i}', inputs=[0], outputs=[], preconditions=[PreconditionAttrEq(object_idx=0, attr='refcount', value=0)]),
        Func(name=f'sub_a_{i}', inputs=[0], outputs=[1,0], mappings=[(0,1)], effects=[
            EffectSetField(output_idx=0, attr='state', value='initialized'),
        ], constraints=[
            ConstraintOutlives(object_idx_a=0, object_idx_b=1)
        ]),
        Func(name=f'new_b_{i}', inputs=[], outputs=[1], effects=[EffectSetField(output_idx=0, attr='state', value='initialized')]),
        Func(name=f'prepare_b_{i}', inputs=[1], outputs=[1], mappings=[(0,0)], effects=[EffectSetField(output_idx=0, attr='state', value='prepared')]),
        Func(name=f'finalize_b_{i}', inputs=[0,1], outputs=[0,1], mappings=[(0,0), (1,1)], effects=[
            EffectSetField(output_idx=0, attr='refcount', value=0),
            EffectSetField(output_idx=1, attr='state', value='finalized'),
        ], preconditions=[
            PreconditionAttrEq(object_idx=0, attr='refcount', value=4),
            PreconditionAttrEq(object_idx=1, attr='state', value='prepared')
        ]),
        Func(name=f'del_b_{i}', inputs=[1], outputs=[], preconditions=[
            PreconditionAttrEq(object_idx=0, attr='state', value='finalized'),
        ]),
    ])


# Maximum live instances per object type
MAX_INSTANCES = 5

# Max number of calls
MAX_CALLS = 20

# Max number of inputs/outputs per call
MAX_ARGS = 5


def AtMostOne(s, vars):
    for i in range(len(vars)):
        for j in range(i+1, len(vars)):
            s.add(Not(And(vars[i], vars[j])))

def ExactlyOne(s, vars):
    AtMostOne(s, vars)
    s.add(Or(vars))


def get_index(m, values):
    opts = [eval(str(m.eval(x))) for x in values]
    assert sum(opts) == 1
    return opts.index(True)


class Encoding:
    def __init__(self, funcs, objects):
        self.funcs = funcs
        self.objects = objects

    def encode(self):
        self.s = Solver()

        # Specifies the function type for each node
        self.node_type = {
            (node_idx, func_idx): Bool(f'node_type_{node_idx}_{func_idx}')
            for node_idx in range(MAX_CALLS)
            for func_idx in range(-1, len(funcs))
        }
        print('node_type', len(self.node_type))

        self.object_alive = {
            (node_idx, object_idx): Bool(f'object_alive_{node_idx}_{object_idx}')
            for node_idx in range(MAX_CALLS + 1)
            for object_idx in range(len(objects) * MAX_INSTANCES)
        }
        print('object_alive', len(self.object_alive))

        self.object_consumed = {
            (node_idx, object_idx): Bool(f'object_consumed_{node_idx}_{object_idx}')
            for node_idx in range(MAX_CALLS + 1)
            for object_idx in range(len(objects) * MAX_INSTANCES)
        }
        print('object_consumed', len(self.object_consumed))

        # Object attributes
        self.object_attributes = {}
        for node_idx in range(MAX_CALLS + 1):
            for object_idx in range(len(objects) * MAX_INSTANCES):
                for attr in objects[object_idx // MAX_INSTANCES].attrs:
                    object_name = objects[object_idx // MAX_INSTANCES].name
                    self.object_attributes[(node_idx, object_idx, attr.name)] = attr.instantiate(self.s, object_name, node_idx, object_idx)
        print('object_attributes', len(self.object_attributes))

        self.input_to_object = {
            (node_idx, input_idx, object_idx): Bool(f'input_to_object_{node_idx}_{input_idx}_{object_idx}')
            for node_idx in range(MAX_CALLS)
            for input_idx in range(-1, MAX_ARGS)
            for object_idx in range(-1, len(objects) * MAX_INSTANCES)
        }
        print('input_to_object', len(self.input_to_object))

        self.output_to_object = {
            (node_idx, output_idx, object_idx): Bool(f'output_to_object_{node_idx}_{output_idx}_{object_idx}')
            for node_idx in range(MAX_CALLS)
            for output_idx in range(-1, MAX_ARGS)
            for object_idx in range(-1, len(objects) * MAX_INSTANCES)
        }
        print('output_to_object', len(self.output_to_object))

        # Each node must have exactly one function type
        print('[*] Exactly one function type per node')
        for node_idx in range(MAX_CALLS):
            ExactlyOne(self.s, [self.node_type[(node_idx, func_idx)] for func_idx in range(-1, len(funcs))])

        # Each input must be mapped to exactly one object (or disconnected)
        print('[*] Exactly one input per node')
        for node_idx in range(MAX_CALLS):
            for input_idx in range(MAX_ARGS):
                ExactlyOne(self.s, [self.input_to_object[(node_idx, input_idx, object_idx)] for object_idx in range(-1, len(objects) * MAX_INSTANCES)])

        # Each output must be mapped to exactly one object (or disconnected)
        print('[*] Exactly one output per node')
        for node_idx in range(MAX_CALLS):
            for output_idx in range(MAX_ARGS):
                ExactlyOne(self.s, [self.output_to_object[(node_idx, output_idx, object_idx)] for object_idx in range(-1, len(objects) * MAX_INSTANCES)])

        # Each real object can be an input or an output at most once
        print('[*] At most one input or output per node per object')
        for node_idx in range(MAX_CALLS):
            for object_idx in range(len(objects) * MAX_INSTANCES):
                AtMostOne(self.s, [self.input_to_object[(node_idx, input_idx, object_idx)] for input_idx in range(MAX_ARGS)])
                AtMostOne(self.s, [self.output_to_object[(node_idx, output_idx, object_idx)] for output_idx in range(MAX_ARGS)])

        # Initial object states are dead
        print('[*] Initial object states are dead')
        for object_idx in range(len(objects) * MAX_INSTANCES):
            self.s.add(Not(self.object_alive[(0, object_idx)]))

        # Final object states are dead
        print('[*] Final object states are dead')
        for object_idx in range(len(objects) * MAX_INSTANCES):
            self.s.add(Not(self.object_alive[(MAX_CALLS, object_idx)]))

        # Nothing is consumed at the start
        print('[*] Nothing is consumed at the start')
        for object_idx in range(len(objects) * MAX_INSTANCES):
            self.s.add(Not(self.object_consumed[(0, object_idx)]))

        # Enforce function number of inputs/outputs
        print('[*] Enforce function number of inputs/outputs')
        for node_idx in range(MAX_CALLS):
            for func_idx in range(len(funcs)):
                cond = self.node_type[(node_idx, func_idx)]
                for idx in range(MAX_ARGS):
                    # Inputs
                    if idx < len(funcs[func_idx].inputs):
                        # Input must be connected to an object of the right type
                        object_idx = funcs[func_idx].inputs[idx]
                        self.s.add(Implies(cond, Or([
                            self.input_to_object[(node_idx, idx, (object_idx * MAX_INSTANCES) + i)]
                            for i in range(MAX_INSTANCES)
                        ])))
                    else:
                        # No input
                        self.s.add(Implies(cond, self.input_to_object[(node_idx, idx, -1)]))

                    # Outputs
                    if idx < len(funcs[func_idx].outputs):
                        # Output must be connected to an object of the right type
                        object_idx = funcs[func_idx].outputs[idx]
                        self.s.add(Implies(cond, Or([
                            self.output_to_object[(node_idx, idx, (object_idx * MAX_INSTANCES) + i)]
                            for i in range(MAX_INSTANCES)
                        ])))
                    else:
                        # No output
                        self.s.add(Implies(cond, self.output_to_object[(node_idx, idx, -1)]))

        # Skip function can have no inputs or outputs
        print('[*] Skip function can have no inputs or outputs')
        for node_idx in range(MAX_CALLS):
            cond = self.node_type[(node_idx, -1)]
            for input_idx in range(MAX_ARGS):
                self.s.add(Implies(cond, self.input_to_object[(node_idx, input_idx, -1)]))
            for output_idx in range(MAX_ARGS):
                self.s.add(Implies(cond, self.output_to_object[(node_idx, output_idx, -1)]))

        # For consistency, enforce mapping input/output must be the same object
        print('[*] For consistency, enforce mapping input/output must be the same object')
        for node_idx in range(MAX_CALLS):
            for func_idx in range(len(funcs)):
                cond = self.node_type[(node_idx, func_idx)]
                
                # if in_x is Z, then out_y must be Z for all x,y
                for in_idx, out_idx in funcs[func_idx].mappings:
                    for object_idx in range(len(objects) * MAX_INSTANCES):
                        self.s.add(Implies(cond, 
                            self.input_to_object[(node_idx, in_idx, object_idx)] == self.output_to_object[(node_idx, out_idx, object_idx)]
                        ))

        # An object can only be an input if it was alive in the previous state
        print('[*] An object can only be an input if it was alive in the previous state')
        for node_idx in range(MAX_CALLS):
            for object_idx in range(len(objects) * MAX_INSTANCES):
                was_alive = self.object_alive[(node_idx, object_idx)]   
                is_input = Or([self.input_to_object[(node_idx, input_idx, object_idx)] for input_idx in range(MAX_ARGS)])
                self.s.add(Implies(is_input, was_alive))

        # An object can only be an output if it was dead in the previous state and not consumed, or was an input this state
        print('[*] An object can only be an output if it was dead in the previous state and not consumed, or was an input this state')
        for node_idx in range(MAX_CALLS):
            for object_idx in range(len(objects) * MAX_INSTANCES):
                was_dead = Not(self.object_alive[(node_idx, object_idx)])
                is_consumed = self.object_consumed[(node_idx, object_idx)]
                is_input = Or([self.input_to_object[(node_idx, input_idx, object_idx)] for input_idx in range(MAX_ARGS)])
                is_output = Or([self.output_to_object[(node_idx, output_idx, object_idx)] for output_idx in range(MAX_ARGS)])
                self.s.add(Implies(is_output, Or([And(was_dead, Not(is_consumed)), is_input])))

        # An object is alive in the state if it was alive in the previous state and not an input this state, or was an output this state
        # An object is consumed if it was alive in the previous state and was an input this state and not an output this state
        print('[*] An object is alive in the state if it was alive in the previous state and not an input this state, or was an output this state')
        print('[*] An object is consumed if it was alive in the previous state and was an input this state and not an output this state')
        for node_idx in range(1, MAX_CALLS+1):
            for object_idx in range(len(objects) * MAX_INSTANCES):
                was_alive = self.object_alive[(node_idx-1, object_idx)]
                was_input = Or([self.input_to_object[(node_idx-1, input_idx, object_idx)] for input_idx in range(MAX_ARGS)])
                was_output = Or([self.output_to_object[(node_idx-1, output_idx, object_idx)] for output_idx in range(MAX_ARGS)])
                was_consumed = self.object_consumed[(node_idx-1, object_idx)]

                is_alive = self.object_alive[(node_idx, object_idx)]
                is_consumed = self.object_consumed[(node_idx, object_idx)]

                self.s.add(is_alive == Or([And(was_alive, Not(was_input)), was_output]))
                self.s.add(is_consumed == Or([was_consumed, And(was_alive, was_input, Not(was_output))]))

        # Object attributes:
        print('[*] Object attributes')
        # Object attribute constructors
        # For a field effect, applies to each instance of the object state
        for node_idx in range(MAX_CALLS):
            for func_idx in range(len(funcs)):
                is_func_active = self.node_type[(node_idx, func_idx)]

                handled_effects = set() # (output_idx, attr)

                for effect in funcs[func_idx].effects:
                    handled_effects.add((effect.output_idx, effect.attr))
                    object_type = funcs[func_idx].outputs[effect.output_idx]
                    for instance_idx in range(MAX_INSTANCES):
                        object_idx = (object_type * MAX_INSTANCES) + instance_idx
                        
                        is_selected_object = self.output_to_object[(node_idx, effect.output_idx, object_idx)]
                        cond = And(is_func_active, is_selected_object)

                        prev_attr = self.object_attributes[(node_idx, object_idx, effect.attr)]
                        next_attr = self.object_attributes[(node_idx+1, object_idx, effect.attr)]

                        attr = [x for x in self.objects[object_type].attrs if x.name == effect.attr][0]

                        match effect:
                            case EffectSetField():
                                self.s.add(Implies(cond, attr.eq(next_attr, attr.const(effect.value))))

                            case EffectIncField():
                                self.s.add(Implies(cond, attr.eq(next_attr, prev_attr + 1)))

                            case EffectDecField():
                                self.s.add(Implies(cond, attr.eq(next_attr, prev_attr - 1)))

                # Any attributes that were not set by an effect are preserved
                for output_idx in range(len(funcs[func_idx].outputs)):
                    object_type = funcs[func_idx].outputs[output_idx]
                    for attr in objects[object_type].attrs:
                        if (output_idx, attr.name) not in handled_effects:
                            for instance_idx in range(MAX_INSTANCES):
                                object_idx = (object_type * MAX_INSTANCES) + instance_idx

                                is_selected_object = self.output_to_object[(node_idx, output_idx, object_idx)]
                                cond = And(is_func_active, is_selected_object)

                                a = self.object_attributes[(node_idx, object_idx, attr.name)]
                                b = self.object_attributes[(node_idx+1, object_idx, attr.name)]
                                self.s.add(Implies(cond, attr.eq(a, b)))


        # If an object was alive, and not used as an input, then copy its attributes to the next state
        print('[*] If an object was alive, and not used as an input, then copy its attributes to the next state')
        for node_idx in range(MAX_CALLS):
            for object_idx in range(len(objects) * MAX_INSTANCES):
                was_alive = self.object_alive[(node_idx, object_idx)]
                is_input = Or([self.input_to_object[(node_idx, input_idx, object_idx)] for input_idx in range(MAX_ARGS)])
                cond = And(was_alive, Not(is_input))

                for attr in objects[object_idx // MAX_INSTANCES].attrs:
                    # self.s.add(Implies(cond, self.object_attributes[(node_idx+1, object_idx, attr.name)] == self.object_attributes[(node_idx, object_idx, attr.name)]))
                    a = self.object_attributes[(node_idx, object_idx, attr.name)]
                    b = self.object_attributes[(node_idx+1, object_idx, attr.name)]
                    self.s.add(Implies(cond, attr.eq(a, b)))

        # Preconditions
        print('[*] Preconditions')
        for node_idx in range(MAX_CALLS):
            for func_idx in range(len(funcs)):
                is_func_active = self.node_type[(node_idx, func_idx)]
                for precondition in funcs[func_idx].preconditions:
                    object_type = funcs[func_idx].inputs[precondition.object_idx]
                    for instance_idx in range(MAX_INSTANCES):
                        object_idx = (object_type * MAX_INSTANCES) + instance_idx

                        is_selected_object = self.input_to_object[(node_idx, precondition.object_idx, object_idx)]
                        cond = And(is_func_active, is_selected_object)

                        attr = [x for x in self.objects[object_type].attrs if x.name == precondition.attr][0]
                        state_before = self.object_attributes[(node_idx, object_idx, precondition.attr)]

                        match precondition:
                            case PreconditionAttrEq():
                                self.s.add(Implies(cond, attr.eq(state_before, attr.const(precondition.value))))

                            case PreconditionAttrGte():
                                self.s.add(Implies(cond, state_before >= precondition.value))


        # Constraints
        print('[*] Constraints')
        for node_idx in range(MAX_CALLS):
            for func_idx in range(len(funcs)):
                is_func_active = self.node_type[(node_idx, func_idx)]
                for constraint in funcs[func_idx].constraints:
                    match constraint:
                        case ConstraintOutlives():
                            # There must be a state n>N where object_idx_a is alive and object_idx_b is consumed
                            for object_instance_a in range(len(objects) * MAX_INSTANCES):
                                for object_instance_b in range(len(objects) * MAX_INSTANCES):
                                    if object_instance_a == object_instance_b:
                                        continue

                                    is_target_a = self.output_to_object[(node_idx, constraint.object_idx_a, object_instance_a)]
                                    is_target_b = self.output_to_object[(node_idx, constraint.object_idx_b, object_instance_b)]

                                    cond = And(is_func_active, is_target_a, is_target_b)

                                    self.s.add(Implies(cond, Or([
                                        And(
                                            self.object_alive[(n, object_instance_a)],
                                            self.object_consumed[(n, object_instance_b)]
                                        )
                                        for n in range(node_idx+1, MAX_CALLS+1)
                                    ])))
    


    def decode_func(self, model: ModelRef, node_idx: int):
        func_idx = get_index(model, [self.node_type[(node_idx, func_idx)] for func_idx in range(-1, len(funcs))]) - 1

        inputs = []
        outputs = []

        for input_idx in range(MAX_ARGS):
            object_idx = get_index(model, [self.input_to_object[(node_idx, input_idx, object_idx)] for object_idx in range(-1, len(objects) * MAX_INSTANCES)]) - 1
            if object_idx == -1:
                pass
            else:
                object_name = objects[object_idx // MAX_INSTANCES].name
                object_idx %= MAX_INSTANCES
                inputs.append(f'{Colors.CYAN}{object_name}_{object_idx}{Colors.END}')

        for output_idx in range(MAX_ARGS):
            object_idx = get_index(model, [self.output_to_object[(node_idx, output_idx, object_idx)] for object_idx in range(-1, len(objects) * MAX_INSTANCES)]) - 1
            if object_idx == -1:
                pass
            else:
                object_name = objects[object_idx // MAX_INSTANCES].name
                object_idx %= MAX_INSTANCES
                outputs.append(f'{Colors.GREEN}{object_name}_{object_idx}{Colors.END}')

        name = funcs[func_idx].name if func_idx != -1 else '<skip>'
        
        # Color the function name based on whether it's a skip or not
        func_color = Colors.LIGHT_GRAY if func_idx == -1 else Colors.YELLOW
        
        print(f'[{Colors.BOLD}{node_idx}{Colors.END}] {func_color}{name}{Colors.END}({", ".join(inputs)}) -> ({", ".join(outputs)})')

    def decode_object_state(self, model: ModelRef, node_idx: int):
        alive = []
        consumed = []
        for object_idx in range(len(objects) * MAX_INSTANCES):
            if eval(str(model.eval(self.object_consumed[(node_idx, object_idx)], model_completion=True))):
                consumed.append(f'{Colors.RED}{objects[object_idx // MAX_INSTANCES].name}_{object_idx % MAX_INSTANCES}{Colors.END}')
                continue

            if not eval(str(model.eval(self.object_alive[(node_idx, object_idx)], model_completion=True))):
                continue

            attributes = {}
            for attr in objects[object_idx // MAX_INSTANCES].attrs:
                # attributes[attr.name] = eval(str(model.eval(self.object_attributes[(node_idx, object_idx, attr.name)], model_completion=True)))
                attr_value = attr.decode(model, self.object_attributes[(node_idx, object_idx, attr.name)])
                attributes[attr.name] = attr_value

            object_name = objects[object_idx // MAX_INSTANCES].name
            object_idx %= MAX_INSTANCES
            
            # Format attributes with color
            attr_strings = []
            for k, v in attributes.items():
                attr_strings.append(f"{Colors.LIGHT_PURPLE}{k}{Colors.END}={Colors.LIGHT_BLUE}{v}{Colors.END}")
            
            alive.append(f'{Colors.BOLD}{object_name}_{object_idx}{Colors.END}({", ".join(attr_strings)})')
        
        print(f'{Colors.BLUE}State{Colors.END} [{Colors.BOLD}{node_idx}{Colors.END}] {", ".join(alive)} Consumed: {", ".join(consumed)}')

    def decode(self, model):
        print(f"\n{Colors.BOLD}{Colors.UNDERLINE}Execution Trace:{Colors.END}")
        for node_idx in range(MAX_CALLS + 1):
            self.decode_object_state(model, node_idx)
            print(f"{Colors.DARK_GRAY}{'─' * 50}{Colors.END}")

            if node_idx < MAX_CALLS:
                self.decode_func(model, node_idx)
                print(f"{Colors.DARK_GRAY}{'─' * 50}{Colors.END}")

    def check(self):
        return self.s.check()

    def model(self):
        return self.s.model()


enc = Encoding(funcs, objects)

enc_start = time.time()
enc.encode()
enc_end = time.time()
print(f'Encoding time: {enc_end - enc_start} seconds')

# One use_a call
# enc.s.add(Or(
#     [enc.node_type[(x, 2)] for x in range(MAX_CALLS)]
# ))
# enc.s.add(Or(
#     [enc.node_type[(x, 3)] for x in range(MAX_CALLS)]
# ))
enc.s.add(Or(
    [enc.node_type[(x, 6)] for x in range(MAX_CALLS)]
))
enc.s.add(enc.node_type[(1, -1)])

check_start = time.time()
print(enc.check())
check_end = time.time()
print(f'Check time: {check_end - check_start} seconds')

model = enc.model()
enc.decode(model)
