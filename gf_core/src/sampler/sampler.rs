use std::collections::{HashMap, HashSet};

use smtlib::{
    backend::z3_binary::Z3Binary, prelude::*, terms::{Const, StaticSorted}, Backend, Bool, Int, Model, SatResultWithModel, Solver, Storage
};

use colored::Colorize;

use crate::{grammar::{EnumDef, EvalCtx, Leaf}, pspec::{PAttr, PAttrType, PSpec}};

use super::sgraph::{SEdge, SGraph, SNode};

/// Encoding specification:
/// 
/// Indexes:
/// - node_idx: index of the node in the call graph
/// - state_idx: index of the state in the call graph (0..max_calls + 1)
///     - state_0 is before node_0, state_1 is after node_0 and before node_1, etc.
/// - func_idx: index of the function in the endpoint list
/// - object_idx: index of the object in the object list
/// - instance_idx: index of the instance of the object (len(object) * max_instances)
/// - input_idx / output_idx: index of the input / output in a given function


pub struct Sampler<'st> {
    pub pspec: PSpec,
    pub st: &'st Storage,
    pub solver: Solver<'st, Z3Binary>,
    pub config: SamplerConfig,

    // (node_idx, func_idx) -> Bool
    // True if the node is of the given function type
    pub node_type: HashMap<(usize, usize), Const<'st, Bool<'st>>>,

    // (state_idx, object_idx) -> Bool
    // True if the object is alive at the given state
    pub object_alive: HashMap<(usize, usize), Const<'st, Bool<'st>>>,

    // (state_idx, object_idx) -> Bool
    // True if the object is consumed at the given state
    pub object_consumed: HashMap<(usize, usize), Const<'st, Bool<'st>>>,

    // (state_idx, instance_idx, attr_name) -> FieldAttr
    // Maps each instance attribute across the states
    pub object_attributes: HashMap<(usize, usize, String), FieldAttr<'st>>,

    // (node_idx, input_idx, instance_idx) -> Bool
    // True if the node's input is mapped to the given instance
    //
    // (node_idx, input_idx, -1) means that node_idx/input_idx is not mapped to any object
    // (node_idx, -1, instance_idx) means that instance is not an input for this node
    pub input_to_object: HashMap<(usize, usize, usize), Const<'st, Bool<'st>>>,

    // (node_idx, output_idx, instance_idx) -> Bool
    // True if the node's output is mapped to the given instance
    pub output_to_object: HashMap<(usize, usize, usize), Const<'st, Bool<'st>>>,
}

pub struct SamplerConfig {
    pub max_calls: usize,
    pub max_args: usize,
    pub instances_per_object: usize,
}

impl Default for SamplerConfig {
    fn default() -> Self {
        Self { max_calls: 10, max_args: 5, instances_per_object: 5 }
    }
}

fn at_most_one<'st, B: Backend>(s: &mut Solver<'st, B>, vars: &[&Bool<'st>]) {
    for i in 0..vars.len() {
        for j in i+1..vars.len() {
            s.assert(!(*vars[i] & *vars[j])).unwrap();
        }
    }
}

fn exactly_one<'st, B: Backend>(s: &mut Solver<'st, B>, vars: &[&Bool<'st>]) {
    at_most_one(s, vars);
    s.assert(vars.iter().fold(Bool::new(s.st(), false), |acc, var| acc | **var)).unwrap();
}

// a -> b
// (b v !a)
fn implies<'st, B: Backend>(s: &mut Solver<'st, B>, a: &Bool<'st>, b: &Bool<'st>) {
    s.assert(*b | (a._eq(false))).unwrap();
}

#[derive(Clone, Debug)]
pub enum FieldAttr<'st> {
    ConstInt(ConstIntAttr<'st>),
    ConstEnum(ConstEnumAttr<'st>),
    VarInt(IntAttr<'st>),
    VarEnum(EnumAttr<'st>),
}

#[derive(Clone, Debug)]
pub struct ConstIntAttr<'st> {
    name: String,
    val: Const<'st, Int<'st>>,
}

#[derive(Clone, Debug)]
pub struct ConstEnumAttr<'st> {
    name: String,
    variants: Vec<String>,
    options: Vec<Const<'st, Bool<'st>>>,
}

#[derive(Clone, Debug)]
pub struct IntAttr<'st> {
    name: String,
    val: Int<'st>,
}

impl<'st> IntAttr<'st> {
    pub fn new_const(st: &'st Storage, val: i64) -> Self {
        Self { name: "int".to_string(), val: Int::new(st, val) }
    }

    pub fn new_var(st: &'st Storage, val: Int<'st>) -> Self {
        Self { name: "int".to_string(), val }
    }
}

#[derive(Clone, Debug)]
pub struct EnumAttr<'st> {
    name: String,
    options: Vec<Bool<'st>>,
}

impl<'st> EnumAttr<'st> {
    pub fn new_var(st: &'st Storage, num_options: usize, idx: usize) -> Self {
        let values = (0..num_options).map(|i| {
            Bool::new(st, i == idx)
        }).collect();
        Self { name: "enum".to_string(), options: values }
    }
}


impl<'st> FieldAttr<'st> {
    pub fn from_pattr<B: Backend>(s: &mut Solver<'st, B>, prefix: &str, pattr: &PAttr) -> Self {
        match &pattr.typ {
            PAttrType::Int => Self::const_int(s, &pattr.name, prefix),
            PAttrType::Enum { variants } => Self::const_enum(s, &pattr.name, prefix, variants.iter().map(|v| v.as_str()).collect()),
        }
    }

    fn const_int<B: Backend>(s: &mut Solver<'st, B>, name: &str, prefix: &str) -> Self {
        Self::ConstInt(ConstIntAttr {
            name: name.to_string(),
            val: Int::new_const(s.st(), prefix),
        })
    }

    fn const_enum<B: Backend>(s: &mut Solver<'st, B>, name: &str, prefix: &str, variants: Vec<&str>) -> Self {
        let options: Vec<_> = (0..variants.len()).map(|idx| Bool::new_const(s.st(), &format!("{}_{}", prefix, idx))).collect();
        exactly_one(s, options.iter().map(|o| &**o).collect::<Vec<_>>().as_slice());
        Self::ConstEnum(ConstEnumAttr {
            name: name.to_string(),
            variants: variants.iter().map(|v| v.to_string()).collect(),
            options,
        })
    }

    fn to_var(self) -> Self {
        match self {
            Self::ConstInt(a) => Self::VarInt(IntAttr { name: a.name, val: *a.val }),
            Self::ConstEnum(a) => Self::VarEnum(EnumAttr { name: a.name, options: a.options.iter().map(|o| **o).collect() }),
            _ => self,
        }
    }

    pub fn select(cond: &Bool<'st>, a: &Self, b: &Self) -> Self {
        match (a, b) {
            (Self::VarInt(a), Self::VarInt(b)) => Self::VarInt(IntAttr {
                name: a.name.clone(),
                val: cond.ite(a.val, b.val),
            }),
            (Self::VarEnum(a), Self::VarEnum(b)) => Self::VarEnum(EnumAttr {
                name: a.name.clone(),
                options: a.options.iter().zip(b.options.iter()).map(|(a, b)| cond.ite(*a, *b)).collect(),
            }),
            _ => panic!("Cannot select between different types"),
        }
    }

    pub fn int_val(&self) -> Int<'st> {
        match self {
            Self::VarInt(a) => a.val,
            _ => panic!("Cannot get int value from non-int attribute"),
        }
    }

    pub fn eq(&self, other: &Self) -> Bool<'st> {
        match (self, other) {
            (Self::VarInt(a), Self::VarInt(b)) => a.val._eq(b.val),
            (Self::VarEnum(a), Self::VarEnum(b)) => {
                a.options.iter().zip(b.options.iter())
                    .map(|(a, b)| a._eq(*b))
                    .fold(Bool::new(a.options[0].st(), true), |acc, b| acc & b)
            },
            _ => panic!("Cannot compare attributes of different types"),
        }
    }

    pub fn decode(self, model: &Model) -> String {
        match self {
            Self::ConstInt(a) => {
                let val = model.eval(a.val).unwrap();
                format!("{}", val)
            }
            Self::ConstEnum(a) => {
                let idx = get_idx(model, a.options.iter().map(|o| *o).collect::<Vec<_>>().as_slice()).unwrap();
                a.variants[idx].to_string()
            }
            _ => panic!("Cannot decode attribute of this type"),
        }
    }
}

fn eval_bool<'st>(model: &Model, value: Const<'st, Bool<'st>>) -> bool {
    match model.eval(value) {
        Some(b) => b.to_string() == "true",
        None => false,
    }
}

fn get_idx<'st>(model: &Model, values: &[Const<'st, Bool<'st>>]) -> Option<usize> {
    values.iter().position(|v| eval_bool(model, *v))
}

impl<'st> Sampler<'st> {
    pub fn new(pspec: PSpec, st: &'st Storage, config: SamplerConfig) -> Self {
        let solver = Solver::new(st, Z3Binary::new("z3").unwrap()).unwrap();
        Self {
            pspec,
            st,
            solver,
            config,
            node_type: HashMap::new(),
            object_alive: HashMap::new(),
            object_consumed: HashMap::new(),
            object_attributes: HashMap::new(),
            input_to_object: HashMap::new(),
            output_to_object: HashMap::new(),
        }
    }

    pub fn setup(&mut self) {
        println!("Setting up sampler");
        self.init_vars();
        println!("Initialized variables");
        self.init_constraints();
        println!("Initialized constraints");
    }

    fn init_vars(&mut self) {
        // Determine which function is called at each node
        for node_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let node_type = Bool::new_const(self.st, &format!("node_type_{}_{}", node_idx, func_idx));
                self.node_type.insert((node_idx, func_idx), node_type);
            }
            let no_func = Bool::new_const(self.st, &format!("node_type_{}_{}", node_idx, usize::MAX));
            self.node_type.insert((node_idx, usize::MAX), no_func);
        }

        // Determine if an object is alive at a given node
        for state_idx in 0..self.config.max_calls + 1 {
            for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let object_alive = Bool::new_const(self.st, &format!("object_alive_{}_{}", state_idx, object_idx));
                self.object_alive.insert((state_idx, object_idx), object_alive);
            }
        }

        // Determine if an object is consumed at a given node
        for state_idx in 0..self.config.max_calls + 1 {
            for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let object_consumed = Bool::new_const(self.st, &format!("object_consumed_{}_{}", state_idx, object_idx));
                self.object_consumed.insert((state_idx, object_idx), object_consumed);
            }
        }

        // Initialize the attributes of each object
        for state_idx in 0..self.config.max_calls + 1 {
            for object_idx in 0..self.pspec.objects.len() {
                for iter_idx in 0..self.config.instances_per_object {
                    let instance_idx = object_idx * self.config.instances_per_object + iter_idx;
                    
                    // Iterate over the attributes of the object
                    for (attr_idx, attr) in self.pspec.objects[object_idx].attrs.iter().enumerate() {
                        self.object_attributes.insert(
                            (state_idx, instance_idx, attr.name.clone()),
                            FieldAttr::from_pattr(
                                &mut self.solver,
                                &format!("attr_{}_{}_{}_{}", object_idx, attr_idx, state_idx, iter_idx),
                                attr
                            )
                        );
                    }
                }
            }
        }

        // Determine if a node's input is mapped to a given object
        for node_idx in 0..self.config.max_calls {
            for input_idx in 0..self.config.max_args {
                for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                    let input_to_object = Bool::new_const(self.st, &format!("input_to_object_{}_{}_{}", node_idx, input_idx, instance_idx));
                    self.input_to_object.insert((node_idx, input_idx, instance_idx), input_to_object);
                }
                // Input is disconnected
                self.input_to_object.insert((node_idx, input_idx, usize::MAX), Bool::new_const(self.st, &format!("input_to_object_{}_{}_{}", node_idx, input_idx, usize::MAX)));
            }

            // Object is disconnected
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                self.input_to_object.insert((node_idx, usize::MAX, instance_idx), Bool::new_const(self.st, &format!("input_to_object_{}_{}_{}", node_idx, usize::MAX, instance_idx)));
            }
        }

        // Determine if a node's output is mapped to a given object
        for node_idx in 0..self.config.max_calls {
            for output_idx in 0..self.config.max_args {
                for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                    let output_to_object = Bool::new_const(self.st, &format!("output_to_object_{}_{}_{}", node_idx, output_idx, instance_idx));
                    self.output_to_object.insert((node_idx, output_idx, instance_idx), output_to_object);
                }
                // Output is disconnected
                self.output_to_object.insert((node_idx, output_idx, usize::MAX), Bool::new_const(self.st, &format!("output_to_object_{}_{}_{}", node_idx, output_idx, usize::MAX)));
            }

            // Object is disconnected
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                self.output_to_object.insert((node_idx, usize::MAX, instance_idx), Bool::new_const(self.st, &format!("output_to_object_{}_{}_{}", node_idx, usize::MAX, instance_idx)));
            }
        }
    }

    fn init_constraints(&mut self) {
        // Each node must have exactly one function type
        for node_idx in 0..self.config.max_calls {
            let vars: Vec<_> = self.node_type.iter()
                .filter(|((k_node_idx, _), _)| node_idx == *k_node_idx)
                .map(|(_, node_type)| &**node_type)
                .collect();
            exactly_one(&mut self.solver, &vars);
        }

        // First node must not be skipped
        self.solver.assert(self.node_type[&(0, usize::MAX)]._eq(false)).unwrap();

        // Skip cannot be followed by a non-skip node
        for node_idx_a in 1..self.config.max_calls {
            for node_idx_b in node_idx_a+1..self.config.max_calls {
                let skip_a = self.node_type[&(node_idx_a, usize::MAX)];
                let skip_b = self.node_type[&(node_idx_b, usize::MAX)];
                implies(&mut self.solver, &skip_a, &skip_b);
            }
        }

        // Initial object states are dead
        for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            self.solver.assert(self.object_alive[&(0, object_idx)]._eq(false)).unwrap();
        }

        // Final object states are dead
        for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            self.solver.assert(self.object_alive[&(self.config.max_calls, object_idx)]._eq(false)).unwrap();
        }

        // Nothing is consumed at the start
        for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            self.solver.assert(self.object_consumed[&(0, object_idx)]._eq(false)).unwrap();
        }

        // Each input must be mapped to exactly one object (or disconnected)
        for node_idx in 0..self.config.max_calls {
            for input_idx in 0..self.config.max_args {
                exactly_one(&mut self.solver, &self.input_to_object.iter()
                    .filter(|((k_node_idx, k_input_idx, _), _)| node_idx == *k_node_idx && input_idx == *k_input_idx)
                    .map(|(_, input_to_object)| &**input_to_object)
                    .collect::<Vec<_>>().as_slice());
            }
        }

        // Each output must be mapped to exactly one object (or disconnected)
        for node_idx in 0..self.config.max_calls {
            for output_idx in 0..self.config.max_args {
                exactly_one(&mut self.solver, &self.output_to_object.iter()
                    .filter(|((k_node_idx, k_output_idx, _), _)| node_idx == *k_node_idx && output_idx == *k_output_idx)
                    .map(|(_, output_to_object)| &**output_to_object)
                    .collect::<Vec<_>>().as_slice());
            }
        }

        // Each real object can be an input or an output at most once
        for node_idx in 0..self.config.max_calls {
            for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                at_most_one(&mut self.solver, &self.input_to_object.iter()
                    .filter(|((k_node_idx, _, k_instance_idx), _)| node_idx == *k_node_idx && object_idx == *k_instance_idx)
                    .map(|(_, input_to_object)| &**input_to_object)
                    .collect::<Vec<_>>().as_slice());
                at_most_one(&mut self.solver, &self.output_to_object.iter()
                    .filter(|((k_node_idx, _, k_instance_idx), _)| node_idx == *k_node_idx && object_idx == *k_instance_idx)
                    .map(|(_, output_to_object)| &**output_to_object)
                    .collect::<Vec<_>>().as_slice());
            }
        }
        
        // Enforce function number of inputs/outputs
        for node_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let cond = self.node_type[&(node_idx, func_idx)];
                
                for inout_idx in 0..self.config.max_args {
                    if inout_idx < self.pspec.endpoints[func_idx].inputs.len() {
                        let object_idx = self.pspec.endpoints[func_idx].inputs[inout_idx];
                        
                        let is_correct_type = (0..self.config.instances_per_object)
                            .map(|i| (object_idx * self.config.instances_per_object) + i)
                            .map(|instance_idx| self.input_to_object[&(node_idx, inout_idx, instance_idx)])
                            .fold(Bool::new(self.st, false), |acc, input_to_object| acc | input_to_object);

                        // If the node is of the correct type, then the input must be mapped to an object of the correct type
                        implies(&mut self.solver, &cond, &is_correct_type);
                    } else {
                        // No input
                        implies(&mut self.solver, &cond, &self.input_to_object[&(node_idx, inout_idx, usize::MAX)]);
                    }

                    if inout_idx < self.pspec.endpoints[func_idx].outputs.len() {
                        let object_idx = self.pspec.endpoints[func_idx].outputs[inout_idx];
                        
                        let is_correct_type = (0..self.config.instances_per_object)
                            .map(|i| (object_idx * self.config.instances_per_object) + i)
                            .map(|instance_idx| self.output_to_object[&(node_idx, inout_idx, instance_idx)])
                            .fold(Bool::new(self.st, false), |acc, output_to_object| acc | output_to_object);

                        // If the node is of the correct type, then the output must be mapped to an object of the correct type
                        implies(&mut self.solver, &cond, &is_correct_type);
                    } else {
                        // No output
                        implies(&mut self.solver, &cond, &self.output_to_object[&(node_idx, inout_idx, usize::MAX)]);
                    }
                }
            }
        }

        // Skip function can have no inputs or outputs
        for node_idx in 0..self.config.max_calls {
            let cond = self.node_type[&(node_idx, usize::MAX)];
            for inout_idx in 0..self.config.max_args {
                implies(&mut self.solver, &cond, &self.input_to_object[&(node_idx, inout_idx, usize::MAX)]);
                implies(&mut self.solver, &cond, &self.output_to_object[&(node_idx, inout_idx, usize::MAX)]);
            }
        }

        // For consistency, enforce mapping input/output must be the same object
        for node_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let cond = self.node_type[&(node_idx, func_idx)];
                
                for mapping in self.pspec.endpoints[func_idx].mappings.iter() {
                    for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                        let is_input = self.input_to_object[&(node_idx, mapping.in_idx, instance_idx)];
                        let is_output = self.output_to_object[&(node_idx, mapping.out_idx, instance_idx)];
                        implies(&mut self.solver, &cond, &is_input._eq(is_output));
                    }
                }
            }
        }

        // An object can only be an input if it was alive in the previous state
        for state_idx in 0..self.config.max_calls {
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let was_alive = self.object_alive[&(state_idx, instance_idx)];
                
                let is_input = (0..self.config.max_args)
                    .map(|input_idx| self.input_to_object[&(state_idx, input_idx, instance_idx)])
                    .fold(Bool::new(self.st, false), |acc, input_to_object| acc | input_to_object);

                implies(&mut self.solver, &is_input, &was_alive);
            }
        }

        // An object can only be an output if it was dead in the previous state and not consumed, or was an input this state
        for state_idx in 0..self.config.max_calls {
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let was_dead = self.object_alive[&(state_idx, instance_idx)]._eq(false);
                let is_consumed = self.object_consumed[&(state_idx, instance_idx)];
                
                let is_input = (0..self.config.max_args)
                    .map(|input_idx| self.input_to_object[&(state_idx, input_idx, instance_idx)])
                    .fold(Bool::new(self.st, false), |acc, input_to_object| acc | input_to_object);

                let is_output = (0..self.config.max_args)
                    .map(|output_idx| self.output_to_object[&(state_idx, output_idx, instance_idx)])
                    .fold(Bool::new(self.st, false), |acc, output_to_object| acc | output_to_object);
            
                implies(&mut self.solver, &is_output, &(is_input | (was_dead & is_consumed._eq(false))));
            }
        }

        // An object is alive in the state if it was alive in the previous state and not an input this state, or was an output this state
        // An object is consumed if it was alive in the previous state and was an input this state and not an output this state
        for state_idx in 1..self.config.max_calls + 1 {
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let was_alive = self.object_alive[&(state_idx - 1, instance_idx)];
                
                let was_input = (0..self.config.max_args)
                    .map(|input_idx| self.input_to_object[&(state_idx - 1, input_idx, instance_idx)])
                    .fold(Bool::new(self.st, false), |acc, input_to_object| acc | input_to_object);

                let was_output = (0..self.config.max_args)
                    .map(|output_idx| self.output_to_object[&(state_idx - 1, output_idx, instance_idx)])
                    .fold(Bool::new(self.st, false), |acc, output_to_object| acc | output_to_object);

                let was_consumed = self.object_consumed[&(state_idx - 1, instance_idx)];

                let is_alive = self.object_alive[&(state_idx, instance_idx)];
                let is_consumed = self.object_consumed[&(state_idx, instance_idx)];

                self.solver.assert(is_alive._eq(((was_alive & was_input._eq(false)) | was_output))).unwrap();
                self.solver.assert(is_consumed._eq(was_consumed | (was_alive & was_input & was_output._eq(false)))).unwrap();
            }
        }

        // Object attributes are preserved unless set by an effect
        for state_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let is_func_active = self.node_type[&(state_idx, func_idx)];

                let mut handled_effects = HashSet::new();

                // Context for evaluating effects
                // Maps Leafs to their FieldAttr
                let mut effect_ctx = HashMap::new();
                for output_idx in 0..self.pspec.endpoints[func_idx].outputs.len() {
                    let object_idx = self.pspec.endpoints[func_idx].outputs[output_idx];
                    
                    for attr in self.pspec.objects[object_idx].attrs.iter() {                        
                        let mut multiplex_attr = self.object_attributes[&(state_idx, object_idx * self.config.instances_per_object, attr.name.clone())].clone().to_var();
                        
                        for iter_idx in 1..self.config.instances_per_object {
                            let instance_idx = object_idx * self.config.instances_per_object + iter_idx;
                            
                            let is_selected_object = self.output_to_object[&(state_idx, output_idx, instance_idx)];
                            let field_attr = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                        
                            multiplex_attr = FieldAttr::select(&is_selected_object, &field_attr.clone().to_var(), &multiplex_attr);
                        }

                        let output_name = &self.pspec.endpoints[func_idx].output_vars[output_idx];
                        effect_ctx.insert(Leaf::Var(output_name.clone(), attr.name.clone()), multiplex_attr);
                    }
                }

                let mut enums = HashMap::new();
                for object in self.pspec.objects.iter() {
                    for attr in object.attrs.iter() {
                        match &attr.typ {
                            PAttrType::Int => {}
                            PAttrType::Enum { variants } => {
                                enums.insert(format!("{}::{}", object.name, attr.name), EnumDef {
                                    name: format!("{}::{}", object.name, attr.name),
                                    variants: variants.clone().into_iter().collect(),
                                });
                            }
                        }
                    }
                }

                let effect_ctx = EvalCtx {
                    st: self.st,
                    vars: effect_ctx,
                    enums,
                };

                for effect in self.pspec.endpoints[func_idx].effects.iter() {
                    let (target_var, target_field) = match &effect.target {
                        Leaf::Var(v, f) => (v, f),
                        _ => {
                            // Ignore global variables and constants
                            println!("Ignoring effect target: {}", effect.target);
                            continue;
                        },
                    };

                    handled_effects.insert((target_var.clone(), target_field.clone()));

                    let output_idx = self.pspec.endpoints[func_idx].output_vars.iter().position(|v| v == target_var).unwrap();
                    let object_idx = self.pspec.endpoints[func_idx].outputs[output_idx];

                    for iter_idx in 0..self.config.instances_per_object {
                        let instance_idx = object_idx * self.config.instances_per_object + iter_idx;

                        let is_selected_object = self.output_to_object[&(state_idx, output_idx, instance_idx)];
                        let cond = is_func_active & is_selected_object;

                        let next_attr = &self.object_attributes[&(state_idx + 1, instance_idx, target_field.clone())];
                        let target_value = effect.expr.eval(&effect_ctx);

                        implies(&mut self.solver, &cond, &target_value.eq(&next_attr.clone().to_var()));
                    }
                }

                // Any attributes that were not set by an effect are preserved
                for output_idx in 0..self.pspec.endpoints[func_idx].outputs.len() {
                    let object_idx = self.pspec.endpoints[func_idx].outputs[output_idx];
                    
                    for attr in self.pspec.objects[object_idx].attrs.iter() {
                        let target_var = &self.pspec.endpoints[func_idx].output_vars[output_idx];
                        let target_field = &attr.name;

                        if handled_effects.contains(&(target_var.clone(), target_field.clone())) {
                            continue;
                        }

                        for iter_idx in 0..self.config.instances_per_object {
                            let instance_idx = object_idx * self.config.instances_per_object + iter_idx;

                            let is_selected_object = self.output_to_object[&(state_idx, output_idx, instance_idx)];
                            let cond = is_func_active & is_selected_object;

                            let prev_attr = &self.object_attributes[&(state_idx, instance_idx, target_field.clone())];
                            let next_attr = &self.object_attributes[&(state_idx + 1, instance_idx, target_field.clone())];
                            
                            let prev_attr_val = prev_attr.clone().to_var(); 
                            let next_attr_val = next_attr.clone().to_var();

                            implies(&mut self.solver, &cond, &prev_attr_val.eq(&next_attr_val));
                        }
                    }
                }
            }
        }

        // If an object was alive, and not used as an input, then copy its attributes to the next state
        for state_idx in 0..self.config.max_calls {
            for object_idx in 0..self.pspec.objects.len() {
                for iter_idx in 0..self.config.instances_per_object {
                    let instance_idx = object_idx * self.config.instances_per_object + iter_idx;

                    let was_alive = self.object_alive[&(state_idx, instance_idx)];
                    let is_input = (0..self.config.max_args)
                        .map(|input_idx| self.input_to_object[&(state_idx, input_idx, instance_idx)])
                        .fold(Bool::new(self.st, false), |acc, input_to_object| acc | input_to_object);

                    let cond = was_alive & is_input._eq(false);

                    for attr in self.pspec.objects[object_idx].attrs.iter() {
                        let prev_attr = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                        let next_attr = &self.object_attributes[&(state_idx + 1, instance_idx, attr.name.clone())];

                        let prev_attr_val = prev_attr.clone().to_var();
                        let next_attr_val = next_attr.clone().to_var();

                        implies(&mut self.solver, &cond, &prev_attr_val.eq(&next_attr_val));
                    }
                }
            }
        }

        // Preconditions
        for state_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let is_func_active = self.node_type[&(state_idx, func_idx)];
                
                // Context for evaluating preconditions
                // Maps Leafs to their FieldAttr
                let mut precondition_ctx = HashMap::new();
                for input_idx in 0..self.pspec.endpoints[func_idx].inputs.len() {
                    let object_idx = self.pspec.endpoints[func_idx].inputs[input_idx];
                    
                    for attr in self.pspec.objects[object_idx].attrs.iter() {                        
                        let mut multiplex_attr = self.object_attributes[&(state_idx, object_idx * self.config.instances_per_object, attr.name.clone())].clone().to_var();
                        
                        for iter_idx in 1..self.config.instances_per_object {
                            let instance_idx = object_idx * self.config.instances_per_object + iter_idx;
                            
                            let is_selected_object = self.input_to_object[&(state_idx, input_idx, instance_idx)];
                            let field_attr = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                        
                            multiplex_attr = FieldAttr::select(&is_selected_object, &field_attr.clone().to_var(), &multiplex_attr);
                        }

                        let input_name = &self.pspec.endpoints[func_idx].input_vars[input_idx];
                        precondition_ctx.insert(Leaf::Var(input_name.clone(), attr.name.clone()), multiplex_attr);
                    }
                }

                let mut enums = HashMap::new();
                for object in self.pspec.objects.iter() {
                    for attr in object.attrs.iter() {
                        match &attr.typ {
                            PAttrType::Int => {}
                            PAttrType::Enum { variants } => {
                                enums.insert(format!("{}::{}", object.name, attr.name), EnumDef {
                                    name: format!("{}::{}", object.name, attr.name),
                                    variants: variants.clone().into_iter().collect(),
                                });
                            }
                        }
                    }
                }

                let precondition_ctx = EvalCtx {
                    st: self.st,
                    vars: precondition_ctx,
                    enums,
                };

                for precondition in self.pspec.endpoints[func_idx].preconditions.iter() {
                    let satisfied = precondition.expr.eval(&precondition_ctx);
                    implies(&mut self.solver, &is_func_active, &satisfied);
                }
            }
        }
    }

    pub fn require_func_active(&mut self, func_idx: usize) {
        let is_func_active = (0..self.config.max_calls)
            .map(|state_idx| self.node_type[&(state_idx, func_idx)])
            .fold(Bool::new(self.st, false), |acc, is_func_active| acc | is_func_active);

        self.solver.assert(is_func_active).unwrap();
    }

    pub fn debug_print(&self, model: &Model) {
        for state_idx in 0..self.config.max_calls + 1 {
            println!("────────────────────────────────────────────────────────────────────────────────");

            self.debug_print_object_state(model, state_idx);

            println!("────────────────────────────────────────────────────────────────────────────────");

            if state_idx < self.config.max_calls {
                self.debug_print_func_call(model, state_idx);
            }
        }
    }

    fn debug_print_func_call(&self, model: &Model, state_idx: usize) {
        let func_idx = get_idx(
            model, 
            &(0..self.pspec.endpoints.len())
                .map(|i| self.node_type[&(state_idx, i)])
                .collect::<Vec<_>>()
        );

        let mut inputs = vec![];
        let mut outputs = vec![];

        for input_idx in 0..self.config.max_args {
            let instance_idx = get_idx(
                model, 
                &(0..self.pspec.objects.len() * self.config.instances_per_object)
                    .map(|i| self.input_to_object[&(state_idx, input_idx, i)])
                    .collect::<Vec<_>>()
            );

            if let Some(instance_idx) = instance_idx {
                let object_idx = instance_idx / self.config.instances_per_object;
                let iter_idx = instance_idx % self.config.instances_per_object;
                inputs.push(format!("{}", format!("{}_{}", self.pspec.objects[object_idx].name, iter_idx).cyan()));
            }
        }

        for output_idx in 0..self.config.max_args {
            let instance_idx = get_idx(
                model, 
                &(0..self.pspec.objects.len() * self.config.instances_per_object)
                    .map(|i| self.output_to_object[&(state_idx, output_idx, i)])
                    .collect::<Vec<_>>()
            );

            if let Some(instance_idx) = instance_idx {
                let object_idx = instance_idx / self.config.instances_per_object;
                let iter_idx = instance_idx % self.config.instances_per_object;
                outputs.push(format!("{}", format!("{}_{}", self.pspec.objects[object_idx].name, iter_idx).cyan()));
            }
        }

        let name = if let Some(func_idx) = func_idx {
            let func = &self.pspec.endpoints[func_idx];
            func.name.clone()
        } else {
            "skip".to_string()
        };

        println!("[{}] {} ({}) -> ({})",
            state_idx.to_string().bold().green(),
            name.yellow(),
            inputs.join(", "),
            outputs.join(", ")
        );
    }

    fn debug_print_object_state(&self, model: &Model, state_idx: usize) {
        let mut alive = vec![];
        let mut consumed = vec![];

        for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            let object_idx = instance_idx / self.config.instances_per_object;
            let iter_idx = instance_idx % self.config.instances_per_object;
            let object_name = &self.pspec.objects[object_idx].name;

            let is_consumed = self.object_consumed[&(state_idx, instance_idx)];
            if eval_bool(model, is_consumed) {
                consumed.push(format!("{}", format!("{}_{}", object_name, iter_idx).red()));
                continue;
            }

            let is_alive = self.object_alive[&(state_idx, instance_idx)];
            if !eval_bool(model, is_alive) {
                continue;
            }

            let mut attributes = vec![];
            for attr in self.pspec.objects[object_idx].attrs.iter() {
                let attr_field = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                let attr_value = attr_field.clone().decode(model);
                attributes.push(format!(
                    "{}={}",
                    attr.name.purple(),
                    attr_value.yellow()
                ));
            }

            alive.push(format!("{}({})", format!("{}_{}", object_name, iter_idx).green(), attributes.join(", ")));
        }

        println!("ALIVE: {} Consumed: {}", alive.join(", "), consumed.join(", "));
    }

    pub fn sample(&mut self, with_pspec: bool) -> Option<SGraph> {
        match self.solver.check_sat_with_model() {
            Ok(SatResultWithModel::Sat(model)) => {
                Some(self.build_sgraph(&model, with_pspec))
            }
            _ => None,
        }
    }

    fn build_sgraph(&self, model: &Model, with_pspec: bool) -> SGraph {
        let mut sgraph = SGraph {
            nodes: vec![],
            edges: vec![],
            pspec: if with_pspec { Some(self.pspec.clone()) } else { None },
        };

        // Keep track of the last node that wrote to an object
        // (object_idx) -> (node_idx, output_idx)
        let mut last_wrote: HashMap<usize, (usize, usize)> = HashMap::new();

        for node_idx in 0..self.config.max_calls {
            let func_idx = get_idx(
                model, 
                &(0..self.pspec.endpoints.len())
                    .map(|i| self.node_type[&(node_idx, i)])
                    .collect::<Vec<_>>()
            );

            if func_idx.is_none() {
                // Skip nodes end the program.
                break;
            }

            let func_idx = func_idx.unwrap();
            let func = &self.pspec.endpoints[func_idx];

            let mut inputs = vec![];
            let mut outputs = vec![];

            for input_idx in 0..func.inputs.len() {
                let instance_idx = get_idx(
                    model, 
                    &(0..self.pspec.objects.len() * self.config.instances_per_object)
                        .map(|i| self.input_to_object[&(node_idx, input_idx, i)])
                        .collect::<Vec<_>>()
                ).unwrap();

                // Find the last node that wrote to this object
                let last_wrote = last_wrote[&instance_idx];
                let last_wrote_node_idx = last_wrote.0;
                let last_wrote_output_idx = last_wrote.1;

                inputs.push(SEdge {
                    other_idx: last_wrote_node_idx,
                    other_conn_idx: last_wrote_output_idx,
                    object_idx: func.inputs[input_idx],
                    attrs: vec![],
                });

                sgraph.nodes[last_wrote_node_idx].outputs[last_wrote_output_idx] = SEdge {
                    other_idx: node_idx,
                    other_conn_idx: input_idx,
                    object_idx: func.inputs[input_idx],
                    attrs: vec![],
                };
            }

            for output_idx in 0..func.outputs.len() {
                let instance_idx = get_idx(
                    model, 
                    &(0..self.pspec.objects.len() * self.config.instances_per_object)
                        .map(|i| self.output_to_object[&(node_idx, output_idx, i)])
                        .collect::<Vec<_>>()
                ).unwrap();

                // Mark the last node that wrote to this object
                last_wrote.insert(instance_idx, (node_idx, output_idx));

                // Placeholder for the output edge
                outputs.push(SEdge {
                    other_idx: usize::MAX,
                    other_conn_idx: usize::MAX,
                    object_idx: func.outputs[output_idx],
                    attrs: vec![],
                });
            }

            let node = SNode {
                func_idx,
                inputs,
                outputs,
            };

            sgraph.nodes.push(node);
        }

        sgraph
    }
}


#[cfg(test)]
mod tests {
    use std::{fs::File, io::Write, process::Command};

    use smtlib::SatResultWithModel;

    use crate::spec::Spec;

    use super::*;
    
    #[test]
    fn test_sampler_single() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "foo",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        },
                        {
                            "name": "y",
                            "type": "enum",
                            "variants": ["A", "B", "C"]
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "init_foo",
                    "variables": [{ "name": "z", "typ": "foo", "dir": "new" }],
                    "template": "z = init_foo();",
                    "preconditions": [],
                    "effects": [
                        "z.x := 0",
                        "z.y := A"
                    ]
                },   
                {
                    "name": "use_foo",
                    "variables": [{ "name": "z", "typ": "foo", "dir": "*" }],
                    "template": "use_foo(z);",
                    "preconditions": [],
                    "effects": [
                        "z.x := z.x + 1"
                    ]
                },
                {
                    "name": "switch_foo",
                    "variables": [{ "name": "z", "typ": "foo", "dir": "*" }],
                    "template": "switch_foo(z);",
                    "preconditions": [
                        "z.x == 2"
                    ],
                    "effects": [
                        "z.y := B"
                    ]
                },
                {
                    "name": "del_foo",
                    "variables": [{ "name": "z", "typ": "foo", "dir": "del" }],
                    "template": "del_foo(z);",
                    "preconditions": [
                        "z.x == 3",
                        "z.y is B"
                    ],
                    "effects": []
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        println!("{}", pspec.pprint());

        let st = Storage::new();
        let config = SamplerConfig::default();
        let mut sampler = Sampler::new(pspec.clone(), &st, config);
        sampler.setup();

        sampler.require_func_active(0);

        let sgraph = sampler.sample(true).unwrap();

        let graph = sgraph.to_graph();
        graph.pprint();

        // Save the sgraph to json
        let sgraph_json = serde_json::to_string(&sgraph).unwrap();
        let mut file = File::create("sgraph.json").unwrap();
        file.write_all(sgraph_json.as_bytes()).unwrap();

        // Write the sgraph to a file
        let mut file = File::create("sgraph.dot").unwrap();
        file.write_all(sgraph.to_dot(&pspec).as_bytes()).unwrap();

        // Run dot on the file and output to PDF
        Command::new("dot")
            .arg("-Tpdf")
            .arg("sgraph.dot")
            .arg("-o")
            .arg("sgraph.pdf")
            .output()
            .unwrap();

        println!("Wrote sgraph to sgraph.dot");
    }

    #[test]
    fn test_sampler_multi() {
        let spec_json = r#"
        {
            "objects": [
                {
                    "name": "number",
                    "attrs": [
                        {
                            "name": "x",
                            "type": "int"
                        },
                        {
                            "name": "state",
                            "type": "enum",
                            "variants": ["FULL", "EMPTY"]
                        }
                    ]
                }
            ],
            "endpoints": [
                {
                    "name": "new_number",
                    "variables": [{ "name": "z", "typ": "number", "dir": "new" }],
                    "template": "z = new_number();",
                    "preconditions": [],
                    "effects": [
                        "z.x := 1",
                        "z.state := FULL"
                    ]
                },   
                {
                    "name": "merge_number",
                    "variables": [
                        { "name": "z", "typ": "number", "dir": "*" },
                        { "name": "w", "typ": "number", "dir": "*" }
                    ],
                    "template": "merge_number(z, w);",
                    "preconditions": [],
                    "effects": [
                        "z.x := z.x + w.x",
                        "z.state := FULL",
                        "w.x := 0",
                        "w.state := EMPTY"
                    ]
                },
                {
                    "name": "discard_number",
                    "variables": [{ "name": "z", "typ": "number", "dir": "del" }],
                    "template": "discard_number(z);",
                    "preconditions": [
                        "z.state is EMPTY"
                    ],
                    "effects": []
                },
                {
                    "name": "submit_number",
                    "variables": [{ "name": "z", "typ": "number", "dir": "del" }],
                    "template": "submit_number(z);",
                    "preconditions": [
                        "z.x == 2",
                        "z.state is FULL"
                    ],
                    "effects": []
                }
            ]
        }
        "#;
        let spec: Spec = serde_json::from_str(spec_json).unwrap();
        let pspec = spec.build_pspec().unwrap();
        println!("{}", pspec.pprint());

        let st = Storage::new();
        let config = SamplerConfig {
            max_calls: 20,
            instances_per_object: 5,
            ..Default::default()
        };
        let mut sampler = Sampler::new(pspec.clone(), &st, config);
        sampler.setup();

        sampler.require_func_active(0);

        let sgraph = sampler.sample(true).unwrap();

        let graph = sgraph.to_graph();
        graph.pprint();

        // Save the sgraph to json
        let sgraph_json = serde_json::to_string(&sgraph).unwrap();
        let mut file = File::create("sgraph.json").unwrap();
        file.write_all(sgraph_json.as_bytes()).unwrap();
        
        // Write the sgraph to a file
        let mut file = File::create("sgraph.dot").unwrap();
        file.write_all(sgraph.to_dot(&pspec).as_bytes()).unwrap();

        // Run dot on the file and output to PDF
        Command::new("dot")
            .arg("-Tpdf")
            .arg("sgraph.dot")
            .arg("-o")
            .arg("sgraph.pdf")
            .output()
            .unwrap();

        println!("Wrote sgraph to sgraph.dot");
    }
}
