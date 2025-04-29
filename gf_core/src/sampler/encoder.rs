use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::rc::Rc;

use rustsat::types::constraints::CardConstraint;
use rustsat::types::{Assignment, Clause, TernaryVal, Var};
use rustsat::instances::{BasicVarManager, Cnf, SatInstance};
use colored::Colorize;
use crate::grammar::{EnumDef, EvalCtx, Leaf};
use crate::pspec::{PAttrType, PEndpointRole, PSpec};
use crate::sampler::util::{and_clause, get_idx, not_clause, or_clause};

use super::expr::FieldAttr;
use super::sgraph::{SEdge, SGraph, SNode};


#[derive(Clone, Debug)]
pub struct EncoderConfig {
    pub max_calls: usize,
    pub max_args: usize,
    pub instances_per_object: usize,
}

impl Default for EncoderConfig {
    fn default() -> Self {
        Self { max_calls: 10, max_args: 10, instances_per_object: 5 }
    }
}

pub struct Encoder {
    pspec: PSpec,
    config: EncoderConfig,

    // (node_idx, func_idx) -> Bool
    // True if the node is of the given function type
    pub node_type: HashMap<(usize, usize), Var>,

    // (state_idx, object_idx) -> Bool
    // True if the object is alive at the given state
    pub object_alive: HashMap<(usize, usize), Var>,

    // (state_idx, object_idx) -> Bool
    // True if the object is consumed at the given state
    pub object_consumed: HashMap<(usize, usize), Var>,

    // (state_idx, instance_idx, attr_name) -> FieldAttr
    // Maps each instance attribute across the states
    pub object_attributes: HashMap<(usize, usize, String), FieldAttr>,

    // (node_idx, input_idx, instance_idx) -> Bool
    // True if the node's input is mapped to the given instance
    //
    // (node_idx, input_idx, -1) means that node_idx/input_idx is not mapped to any object
    // (node_idx, -1, instance_idx) means that instance is not an input for this node
    pub input_to_object: HashMap<(usize, usize, usize), Var>,

    // (node_idx, output_idx, instance_idx) -> Bool
    // True if the node's output is mapped to the given instance
    pub output_to_object: HashMap<(usize, usize, usize), Var>,
}

impl Encoder {
    pub fn create(pspec: PSpec, config: EncoderConfig) -> (Self, Cnf, BasicVarManager) {
        let mut encoder = Self::new(pspec, config);
        let base_instance = encoder.base_encoding();
        let (base_cnf, base_vm) = base_instance.into_cnf();
        (encoder, base_cnf, base_vm)
    }

    fn new(pspec: PSpec, config: EncoderConfig) -> Self {
        Self { 
            pspec,
            config,
            node_type: HashMap::new(),
            object_alive: HashMap::new(),
            object_consumed: HashMap::new(),
            object_attributes: HashMap::new(),
            input_to_object: HashMap::new(),
            output_to_object: HashMap::new(),
        }
    }

    // Initialize variables and returns the base encoding
    fn base_encoding(&mut self) -> SatInstance {
        let instance = Rc::new(RefCell::new(SatInstance::new()));
        self.init_vars(instance.clone());
        self.init_constraints(instance.clone());
        instance.take()
    }

    fn init_vars(&mut self, instance: Rc<RefCell<SatInstance>>) {
        // Determine which function is called at each node
        for node_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                self.node_type.insert((node_idx, func_idx), instance.borrow_mut().new_var());
            }
            self.node_type.insert((node_idx, usize::MAX), instance.borrow_mut().new_var());
        }

        // Determine if an object is alive at a given node
        for state_idx in 0..self.config.max_calls + 1 {
            for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                self.object_alive.insert((state_idx, object_idx), instance.borrow_mut().new_var());
            }
        }

        // Determine if an object is consumed at a given node
        for state_idx in 0..self.config.max_calls + 1 {
            for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                self.object_consumed.insert((state_idx, object_idx), instance.borrow_mut().new_var());
            }
        }

        // Initialize the attributes of each object
        for state_idx in 0..self.config.max_calls + 1 {
            for object_idx in 0..self.pspec.objects.len() {
                for iter_idx in 0..self.config.instances_per_object {
                    let instance_idx = object_idx * self.config.instances_per_object + iter_idx;
                    
                    // Iterate over the attributes of the object
                    for attr in self.pspec.objects[object_idx].attrs.iter() {
                        self.object_attributes.insert(
                            (state_idx, instance_idx, attr.name.clone()),
                            FieldAttr::from_pattr(
                                &mut instance.borrow_mut(),
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
                    self.input_to_object.insert((node_idx, input_idx, instance_idx), instance.borrow_mut().new_var());
                }
                // Input is disconnected
                self.input_to_object.insert((node_idx, input_idx, usize::MAX), instance.borrow_mut().new_var());
            }

            // Object is disconnected
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                self.input_to_object.insert((node_idx, usize::MAX, instance_idx), instance.borrow_mut().new_var());
            }
        }

        // Determine if a node's output is mapped to a given object
        for node_idx in 0..self.config.max_calls {
            for output_idx in 0..self.config.max_args {
                for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                    self.output_to_object.insert((node_idx, output_idx, instance_idx), instance.borrow_mut().new_var());
                }
                // Output is disconnected
                self.output_to_object.insert((node_idx, output_idx, usize::MAX), instance.borrow_mut().new_var());
            }

            // Object is disconnected
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                self.output_to_object.insert((node_idx, usize::MAX, instance_idx), instance.borrow_mut().new_var());
            }
        }
    }

    fn init_constraints(&mut self, instance: Rc<RefCell<SatInstance>>) {
        // Each node must have exactly one function type
        println!("[*] Initializing node type constraints");
        for node_idx in 0..self.config.max_calls {
            let vars = self.node_type.iter()
                .filter(|((k_node_idx, _), _)| node_idx == *k_node_idx)
                .map(|(_, node_type)| node_type.pos_lit());
            instance.borrow_mut().add_card_constr(CardConstraint::new_eq(vars, 1));
        }

        // First node must not be skipped
        instance.borrow_mut().add_unit(self.node_type[&(0, usize::MAX)].neg_lit());

        // Skip cannot be followed by a non-skip node
        println!("[*] Initializing skip constraints");
        for node_idx_a in 1..self.config.max_calls {
            for node_idx_b in node_idx_a+1..self.config.max_calls {
                let skip_a = self.node_type[&(node_idx_a, usize::MAX)];
                let skip_b = self.node_type[&(node_idx_b, usize::MAX)];

                // skip_a -> skip_b
                instance.borrow_mut().add_binary(skip_a.neg_lit(), skip_b.pos_lit());
            }
        }

        // Initial object states are dead
        println!("[*] Initializing initial object state constraints");
        for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            instance.borrow_mut().add_unit(self.object_alive[&(0, object_idx)].neg_lit());
        }

        // Final object states are dead
        println!("[*] Initializing final object state constraints");
        for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            instance.borrow_mut().add_unit(self.object_alive[&(self.config.max_calls, object_idx)].neg_lit());
        }

        // Nothing is consumed at the start
        println!("[*] Initializing initial object consumption constraints");
        for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            instance.borrow_mut().add_unit(self.object_consumed[&(0, object_idx)].neg_lit());
        }

        // Each input must be mapped to exactly one object (or disconnected)
        println!("[*] Initializing input mapping constraints");
        for node_idx in 0..self.config.max_calls {
            println!("[*] Initializing input mapping constraints for node {}", node_idx);
            for input_idx in 0..self.config.max_args {
                println!("[*] Initializing input mapping constraints for node {} input {}", node_idx, input_idx);
                
                let vars = self.input_to_object.iter()
                    .filter(|((k_node_idx, k_input_idx, _), _)| node_idx == *k_node_idx && input_idx == *k_input_idx)
                    .map(|(_, input_to_object)| input_to_object.pos_lit());
                
                instance.borrow_mut().add_card_constr(CardConstraint::new_eq(vars, 1));
            }
        }

        // Each output must be mapped to exactly one object (or disconnected)
        println!("[*] Initializing output mapping constraints");
        for node_idx in 0..self.config.max_calls {
            println!("[*] Initializing output mapping constraints for node {}", node_idx);
            for output_idx in 0..self.config.max_args {
                println!("[*] Initializing output mapping constraints for node {} output {}", node_idx, output_idx);

                let vars = self.output_to_object.iter()
                    .filter(|((k_node_idx, k_output_idx, _), _)| node_idx == *k_node_idx && output_idx == *k_output_idx)
                    .map(|(_, output_to_object)| output_to_object.pos_lit());
                
                instance.borrow_mut().add_card_constr(CardConstraint::new_eq(vars, 1));
            }
        }

        // Each real object can be an input or an output at most once
        println!("[*] Initializing object mapping constraints");
        for node_idx in 0..self.config.max_calls {
            for object_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let input_vars = self.input_to_object.iter()
                    .filter(|((k_node_idx, _, k_instance_idx), _)| node_idx == *k_node_idx && object_idx == *k_instance_idx)
                    .map(|(_, input_to_object)| input_to_object.pos_lit());
                
                let output_vars = self.output_to_object.iter()
                    .filter(|((k_node_idx, _, k_instance_idx), _)| node_idx == *k_node_idx && object_idx == *k_instance_idx)
                    .map(|(_, output_to_object)| output_to_object.pos_lit());

                instance.borrow_mut().add_card_constr(CardConstraint::new_ub(input_vars, 1));
                instance.borrow_mut().add_card_constr(CardConstraint::new_ub(output_vars, 1));
            }
        }
        
        // Enforce function number of inputs/outputs
        println!("[*] Initializing function input/output constraints");
        for node_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let cond = self.node_type[&(node_idx, func_idx)];
                
                for inout_idx in 0..self.config.max_args {
                    if inout_idx < self.pspec.endpoints[func_idx].inputs.len() {
                        let object_idx = self.pspec.endpoints[func_idx].inputs[inout_idx];
                        
                        let correct_object_vars = (0..self.config.instances_per_object)
                            .map(|i: usize| (object_idx * self.config.instances_per_object) + i)
                            .map(|instance_idx| self.input_to_object[&(node_idx, inout_idx, instance_idx)])
                            .collect::<Vec<_>>();

                        let correct_input = or_clause(&mut instance.borrow_mut(), &correct_object_vars);

                        // If the node is of the correct type, then the input must be mapped to an object of the correct type
                        // cond -> x
                        instance.borrow_mut().add_binary(cond.neg_lit(), correct_input.pos_lit());
                    } else {
                        // No input
                        instance.borrow_mut().add_binary(cond.neg_lit(), self.input_to_object[&(node_idx, inout_idx, usize::MAX)].pos_lit());
                    }

                    if inout_idx < self.pspec.endpoints[func_idx].outputs.len() {
                        let object_idx = self.pspec.endpoints[func_idx].outputs[inout_idx];

                        let correct_object_vars = (0..self.config.instances_per_object)
                            .map(|i: usize| (object_idx * self.config.instances_per_object) + i)
                            .map(|instance_idx| self.output_to_object[&(node_idx, inout_idx, instance_idx)])
                            .collect::<Vec<_>>();

                        let correct_output = or_clause(&mut instance.borrow_mut(), &correct_object_vars);

                        // If the node is of the correct type, then the output must be mapped to an object of the correct type
                        // cond -> x
                        instance.borrow_mut().add_binary(cond.neg_lit(), correct_output.pos_lit());
                    } else {
                        // No output
                        instance.borrow_mut().add_binary(cond.neg_lit(), self.output_to_object[&(node_idx, inout_idx, usize::MAX)].pos_lit());
                    }
                }
            }
        }

        // Skip function can have no inputs or outputs
        println!("[*] Initializing skip function constraints");
        for node_idx in 0..self.config.max_calls {
            let cond = self.node_type[&(node_idx, usize::MAX)];
            for inout_idx in 0..self.config.max_args {
                instance.borrow_mut().add_binary(cond.neg_lit(), self.input_to_object[&(node_idx, inout_idx, usize::MAX)].pos_lit());
                instance.borrow_mut().add_binary(cond.neg_lit(), self.output_to_object[&(node_idx, inout_idx, usize::MAX)].pos_lit());
            }
        }

        // For consistency, enforce mapping input/output must be the same object
        println!("[*] Initializing input/output consistency constraints");
        for node_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let cond = self.node_type[&(node_idx, func_idx)];
                
                for mapping in self.pspec.endpoints[func_idx].mappings.iter() {
                    for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                        let is_input = self.input_to_object[&(node_idx, mapping.in_idx, instance_idx)];
                        let is_output = self.output_to_object[&(node_idx, mapping.out_idx, instance_idx)];

                        // cond -> (is_input == is_output)
                        instance.borrow_mut().add_ternary(cond.neg_lit(), is_input.neg_lit(), is_output.pos_lit());
                        instance.borrow_mut().add_ternary(cond.neg_lit(), is_input.pos_lit(), is_output.neg_lit());
                    }
                }
            }
        }

        // An object can only be an input if it was alive in the previous state
        println!("[*] Initializing input constraints");
        for state_idx in 0..self.config.max_calls {
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let was_alive = self.object_alive[&(state_idx, instance_idx)];
                
                let input_vars = (0..self.config.max_args)
                    .map(|input_idx| self.input_to_object[&(state_idx, input_idx, instance_idx)])
                    .collect::<Vec<_>>();

                let is_input = or_clause(&mut instance.borrow_mut(), &input_vars);

                // is_input -> was_alive
                instance.borrow_mut().add_binary(is_input.neg_lit(), was_alive.pos_lit());
            }
        }

        // An object can only be an output if it was dead in the previous state and not consumed, or was an input this state
        println!("[*] Initializing output constraints");
        for state_idx in 0..self.config.max_calls {
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let was_dead = self.object_alive[&(state_idx, instance_idx)].neg_lit();
                let is_consumed = self.object_consumed[&(state_idx, instance_idx)];
                
                let input_vars = (0..self.config.max_args)
                    .map(|input_idx| self.input_to_object[&(state_idx, input_idx, instance_idx)])
                    .collect::<Vec<_>>();

                let is_input = or_clause(&mut instance.borrow_mut(), &input_vars);

                let output_vars = (0..self.config.max_args)
                    .map(|output_idx| self.output_to_object[&(state_idx, output_idx, instance_idx)])
                    .collect::<Vec<_>>();

                let is_output = or_clause(&mut instance.borrow_mut(), &output_vars);

                // is_output -> (is_input | (was_dead & !is_consumed))
                instance.borrow_mut().add_ternary(is_output.neg_lit(), is_input.pos_lit(), was_dead);
                instance.borrow_mut().add_ternary(is_output.neg_lit(), is_input.neg_lit(), is_consumed.neg_lit());
            }
        }

        // An object is alive in the state if it was alive in the previous state and not an input this state, or was an output this state
        // An object is consumed if it was alive in the previous state and was an input this state and not an output this state
        println!("[*] Initializing object state constraints");
        for state_idx in 1..self.config.max_calls + 1 {
            for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
                let was_alive = self.object_alive[&(state_idx - 1, instance_idx)];
                
                let input_vars = (0..self.config.max_args)
                    .map(|input_idx| self.input_to_object[&(state_idx - 1, input_idx, instance_idx)])
                    .collect::<Vec<_>>();

                let was_input = or_clause(&mut instance.borrow_mut(), &input_vars);

                let output_vars = (0..self.config.max_args)
                    .map(|output_idx| self.output_to_object[&(state_idx - 1, output_idx, instance_idx)])
                    .collect::<Vec<_>>();

                let was_output = or_clause(&mut instance.borrow_mut(), &output_vars);

                let was_consumed = self.object_consumed[&(state_idx - 1, instance_idx)];

                let is_alive = self.object_alive[&(state_idx, instance_idx)];
                let is_consumed = self.object_consumed[&(state_idx, instance_idx)];

                // is_alive == (was_output | (was_alive & !was_input))
                //   (¬is_alive ∨ was_output ∨ was_alive)
                // ∧ (¬is_alive ∨ was_output ∨ ¬was_input)
                // ∧ (¬was_output ∨ is_alive)
                // ∧ (¬was_alive ∨ was_input ∨ is_alive)
                instance.borrow_mut().add_ternary(is_alive.neg_lit(), was_output.pos_lit(), was_alive.pos_lit());
                instance.borrow_mut().add_ternary(is_alive.neg_lit(), was_output.pos_lit(), was_input.neg_lit());
                instance.borrow_mut().add_binary(was_output.neg_lit(), is_alive.pos_lit());
                instance.borrow_mut().add_ternary(was_alive.neg_lit(), was_input.pos_lit(), is_alive.pos_lit());

                // is_consumed == (was_consumed | (was_alive & was_input & !was_output))
                //   (¬is_consumed ∨ was_consumed ∨ was_alive)
                // ∧ (¬is_consumed ∨ was_consumed ∨ was_input)
                // ∧ (¬is_consumed ∨ was_consumed ∨ ¬was_output)
                // ∧ (¬was_consumed ∨ is_consumed)
                // ∧ (¬was_alive ∨ ¬was_input ∨ was_output ∨ is_consumed)
                instance.borrow_mut().add_ternary(is_consumed.neg_lit(), was_consumed.pos_lit(), was_alive.pos_lit());
                instance.borrow_mut().add_ternary(is_consumed.neg_lit(), was_consumed.pos_lit(), was_input.pos_lit());
                instance.borrow_mut().add_ternary(is_consumed.neg_lit(), was_consumed.pos_lit(), was_output.neg_lit());
                instance.borrow_mut().add_binary(was_consumed.neg_lit(), is_consumed.pos_lit());
                instance.borrow_mut().add_clause(Clause::from([
                    was_alive.neg_lit(),
                    was_input.neg_lit(),
                    was_output.pos_lit(),
                    is_consumed.pos_lit(),
                ]));
            }
        }

        // Object attributes are preserved unless set by an effect
        println!("[*] Initializing object attribute constraints");
        for state_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let is_func_active = self.node_type[&(state_idx, func_idx)];

                // Keep track of explicit effects so we ensure all other fields are preserved
                let mut handled_effects = HashSet::new();

                // Context for evaluating effects
                // Maps Leafs to their FieldAttr
                let mut effect_ctx = HashMap::new();
                for output_idx in 0..self.pspec.endpoints[func_idx].outputs.len() {
                    let object_idx = self.pspec.endpoints[func_idx].outputs[output_idx];
                    
                    for attr in self.pspec.objects[object_idx].attrs.iter() {                        
                        let mut multiplex_attr = self.object_attributes[&(state_idx, object_idx * self.config.instances_per_object, attr.name.clone())].clone();
                        
                        for iter_idx in 1..self.config.instances_per_object {
                            let instance_idx = object_idx * self.config.instances_per_object + iter_idx;
                            
                            let is_selected_object = self.output_to_object[&(state_idx, output_idx, instance_idx)];
                            let field_attr = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                        
                            multiplex_attr = FieldAttr::select(&mut instance.borrow_mut(), &is_selected_object, &field_attr, &multiplex_attr);
                        }

                        let output_name = &self.pspec.endpoints[func_idx].output_vars[output_idx];
                        println!("Multiplexed attribute: {:?}", multiplex_attr);
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
                    vars: effect_ctx,
                    enums,
                    instance: instance.clone(),
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
                        let cond = and_clause(&mut effect_ctx.instance.borrow_mut(), &[is_selected_object, is_func_active]);

                        let next_attr = &self.object_attributes[&(state_idx + 1, instance_idx, target_field.clone())];
                        let target_value = effect.expr.eval(&effect_ctx);

                        let eq = next_attr.eq(&target_value, &mut effect_ctx.instance.borrow_mut());

                        effect_ctx.instance.borrow_mut().add_lit_impl_lit(cond.pos_lit(), eq.pos_lit());
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
                            let cond = and_clause(&mut effect_ctx.instance.borrow_mut(), &[is_func_active, is_selected_object]);

                            let prev_attr = &self.object_attributes[&(state_idx, instance_idx, target_field.clone())];
                            let next_attr = &self.object_attributes[&(state_idx + 1, instance_idx, target_field.clone())];
                            
                            let eq = prev_attr.eq(next_attr, &mut effect_ctx.instance.borrow_mut());

                            effect_ctx.instance.borrow_mut().add_lit_impl_lit(cond.pos_lit(), eq.pos_lit());
                        }
                    }
                }
            }
        }

        // If an object was alive, and not used as an input, then copy its attributes to the next state
        println!("[*] Initializing object attribute copy constraints");
        for state_idx in 0..self.config.max_calls {
            for object_idx in 0..self.pspec.objects.len() {
                for iter_idx in 0..self.config.instances_per_object {
                    let instance_idx = object_idx * self.config.instances_per_object + iter_idx;

                    let was_alive = self.object_alive[&(state_idx, instance_idx)];

                    let input_vars = (0..self.config.max_args)
                        .map(|input_idx| self.input_to_object[&(state_idx, input_idx, instance_idx)])
                        .collect::<Vec<_>>();

                    let is_input = or_clause(&mut instance.borrow_mut(), &input_vars);
                    let not_input = not_clause(&mut instance.borrow_mut(), &is_input);

                    let cond = and_clause(&mut instance.borrow_mut(), &[was_alive, not_input]);

                    for attr in self.pspec.objects[object_idx].attrs.iter() {
                        let prev_attr = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                        let next_attr = &self.object_attributes[&(state_idx + 1, instance_idx, attr.name.clone())];

                        let eq = prev_attr.eq(next_attr, &mut instance.borrow_mut());

                        instance.borrow_mut().add_lit_impl_lit(cond.pos_lit(), eq.pos_lit());
                    }
                }
            }
        }

        // Preconditions
        println!("[*] Initializing preconditions");
        for state_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let is_func_active = self.node_type[&(state_idx, func_idx)];
                
                // Context for evaluating preconditions
                // Maps Leafs to their FieldAttr
                let mut precondition_ctx = HashMap::new();
                for input_idx in 0..self.pspec.endpoints[func_idx].inputs.len() {
                    let object_idx = self.pspec.endpoints[func_idx].inputs[input_idx];
                    
                    for attr in self.pspec.objects[object_idx].attrs.iter() {                        
                        let mut multiplex_attr = self.object_attributes[&(state_idx, object_idx * self.config.instances_per_object, attr.name.clone())].clone();
                        
                        for iter_idx in 1..self.config.instances_per_object {
                            let instance_idx = object_idx * self.config.instances_per_object + iter_idx;
                            
                            let is_selected_object = self.input_to_object[&(state_idx, input_idx, instance_idx)];
                            let field_attr = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                        
                            multiplex_attr = FieldAttr::select(&mut instance.borrow_mut(), &is_selected_object, &field_attr, &multiplex_attr);
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
                    instance: instance.clone(),
                    vars: precondition_ctx,
                    enums,
                };

                for precondition in self.pspec.endpoints[func_idx].preconditions.iter() {
                    let satisfied = precondition.expr.eval(&precondition_ctx);
                    instance.borrow_mut().add_lit_impl_lit(is_func_active.pos_lit(), satisfied.pos_lit());
                }
            }
        }
    
        // Outlives
        println!("[*] Initializing outlives constraints");
        for state_idx in 0..self.config.max_calls {
            for func_idx in 0..self.pspec.endpoints.len() {
                let is_func_active = self.node_type[&(state_idx, func_idx)];
                let func = &self.pspec.endpoints[func_idx];

                for outlives in func.outlives.iter() {
                    let output_idx_a = func.output_vars.iter().position(|o| o == &outlives.a).unwrap();
                    let output_idx_b = func.output_vars.iter().position(|o| o == &outlives.b).unwrap();
                    
                    let object_idx_a = func.outputs[output_idx_a];
                    let object_idx_b = func.outputs[output_idx_b];

                    for iter_idx_a in 0..self.config.instances_per_object {
                        for iter_idx_b in 0..self.config.instances_per_object {
                            let instance_idx_a = object_idx_a * self.config.instances_per_object + iter_idx_a;
                            let instance_idx_b = object_idx_b * self.config.instances_per_object + iter_idx_b;

                            let is_target_a = self.output_to_object[&(state_idx, output_idx_a, instance_idx_a)];
                            let is_target_b = self.output_to_object[&(state_idx, output_idx_b, instance_idx_b)];

                            let cond = and_clause(&mut instance.borrow_mut(), &[is_func_active, is_target_a, is_target_b]);

                            let mut options = vec![];
                            for next_state_idx in state_idx + 1..self.config.max_calls+1 {
                                let is_alive_a = self.object_alive[&(next_state_idx, instance_idx_a)];
                                let is_consumed_b = self.object_consumed[&(next_state_idx, instance_idx_b)];

                                options.push(and_clause(&mut instance.borrow_mut(), &[is_alive_a, is_consumed_b]));
                            }

                            let outlives = or_clause(&mut instance.borrow_mut(), &options);

                            instance.borrow_mut().add_lit_impl_lit(cond.pos_lit(), outlives.pos_lit());
                        }
                    }
                }
            }
        }
    
        // Library initializers must be called and must be the first function(s) called, and cannot be called more than once.
        println!("[*] Initializing library initializer constraints");
        let initializer_idxs = self.pspec.endpoints.iter().enumerate()
            .filter(|(_, e)| matches!(e.role, PEndpointRole::LibraryInitializer))
            .map(|(idx, _)| idx)
            .collect::<Vec<_>>();

        assert!(initializer_idxs.len() <= self.config.max_calls, "Too many library initializers for the number of calls");

        // First N functions must be initializers
        println!("[*] Initializing library initializer constraints (first N)");
        for node_idx in 0..initializer_idxs.len() {
            let cond = or_clause(&mut instance.borrow_mut(), &initializer_idxs.iter()
                .map(|idx| self.node_type[&(node_idx, *idx)])
                .collect::<Vec<_>>());

            instance.borrow_mut().add_unit(cond.pos_lit());
        }

        // Remaining functions must not be initializers
        println!("[*] Initializing library initializer constraints (remaining)");
        for node_idx in initializer_idxs.len()..self.config.max_calls {
            let cond = or_clause(&mut instance.borrow_mut(), &initializer_idxs.iter()
                .map(|idx| self.node_type[&(node_idx, *idx)])
                .collect::<Vec<_>>());

            instance.borrow_mut().add_unit(cond.neg_lit());
        }

        // Library initializers cannot be called more than once
        // (we can optimize by checking only the first N functions)
        println!("[*] Initializing library initializer constraints (no more than once)");
        for initializer_idx in 0..initializer_idxs.len() {
            for node_a in 0..initializer_idxs.len() {
                for node_b in initializer_idx + 1..initializer_idxs.len() {
                    let is_initializer_a = self.node_type[&(node_a, initializer_idx)];
                    let is_initializer_b = self.node_type[&(node_b, initializer_idx)];
                    
                    let cond = and_clause(&mut instance.borrow_mut(), &[is_initializer_a, is_initializer_b]);
                    instance.borrow_mut().add_unit(cond.neg_lit());
                }
            }
        }
    }

    pub fn encode_func_active(&self, instance: &mut SatInstance, func_idx: usize) {
        let is_func_active = or_clause(instance, &(0..self.config.max_calls).map(|i| self.node_type[&(i, func_idx)]).collect::<Vec<_>>());
        instance.add_unit(is_func_active.pos_lit());
    }

    pub fn encode_blocking(&self, instance: &mut SatInstance, graph: &SGraph) {
        // At least one of the node types must be different from the graph
        let mut comps = vec![];
        for node_idx in 0..self.config.max_calls {
            let node_type = if node_idx < graph.nodes.len() {
                graph.nodes[node_idx].func_idx
            } else {
                usize::MAX
            };

            let is_same = self.node_type[&(node_idx, node_type)];
            comps.push(is_same);
        }

        let all_same = and_clause(instance, &comps);
        instance.add_unit(all_same.neg_lit());
    }

    pub fn debug_print(&self, assignment: &Assignment) {
        for state_idx in 0..self.config.max_calls + 1 {
            println!("────────────────────────────────────────────────────────────────────────────────");

            self.debug_print_object_state(assignment, state_idx);

            println!("────────────────────────────────────────────────────────────────────────────────");

            if state_idx < self.config.max_calls {
                self.debug_print_func_call(assignment, state_idx);
            }
        }
    }

    fn debug_print_func_call(&self, assignment: &Assignment, state_idx: usize) {
        let func_idx = get_idx(
            assignment, 
            &(0..self.pspec.endpoints.len())
                .map(|i| self.node_type[&(state_idx, i)])
                .collect::<Vec<_>>()
        );

        let mut inputs = vec![];
        let mut outputs = vec![];

        for input_idx in 0..self.config.max_args {
            let instance_idx = get_idx(
                assignment,
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
                assignment, 
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

    fn debug_print_object_state(&self, assignment: &Assignment, state_idx: usize) {
        let mut alive = vec![];
        let mut consumed = vec![];

        for instance_idx in 0..(self.pspec.objects.len() * self.config.instances_per_object) {
            let object_idx = instance_idx / self.config.instances_per_object;
            let iter_idx = instance_idx % self.config.instances_per_object;
            let object_name = &self.pspec.objects[object_idx].name;

            let is_consumed = self.object_consumed[&(state_idx, instance_idx)];
            if assignment.var_value(is_consumed) == TernaryVal::True {
                consumed.push(format!("{}", format!("{}_{}", object_name, iter_idx).red()));
                continue;
            }

            let is_alive = self.object_alive[&(state_idx, instance_idx)];
            if assignment.var_value(is_alive) != TernaryVal::True {
                continue;
            }

            let mut attributes: Vec<String> = vec![];
            for attr in self.pspec.objects[object_idx].attrs.iter() {
                let attr_field = &self.object_attributes[&(state_idx, instance_idx, attr.name.clone())];
                let attr_value = attr_field.decode(&assignment);
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

    pub fn decode_sgraph(&self, assignment: &Assignment, with_pspec: bool) -> SGraph {
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
                assignment, 
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
                    assignment, 
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
                    assignment, 
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
