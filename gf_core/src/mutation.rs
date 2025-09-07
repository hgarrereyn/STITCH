use std::{cell::RefCell, collections::{HashMap, HashSet}, fs::File, path::Path, usize};

use rand::{seq::IteratorRandom, Rng};
use rand::seq::SliceRandom;
use serde::{Deserialize, Serialize};
use serde_with::serde_as;

use crate::pspec::PSpec;

use super::graph::{Graph, NodeRefType};


// (type, depth, forward)
pub type SchemaRef = (usize, usize, bool);

#[serde_as]
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SchemaCache {
    #[serde_as(as = "Vec<(_,_)>")]
    pub nodes: HashMap<SchemaRef, TypeNode>,
    pub enabled_endpoints: Vec<usize>,
}

impl SchemaCache {
    pub fn new() -> Self {
        Self {
            nodes: HashMap::new(),
            enabled_endpoints: Vec::new(),
        }
    }

    pub fn get_type_node(&self, typ: usize, depth: usize, forward: bool) -> Option<&TypeNode> {
        self.nodes.get(&(typ, depth, forward))
    }

    pub fn smallest_depth(&self, typ: usize, forward: bool) -> Option<usize> {
        self.nodes.iter()
            .filter(|(key, node)| key.0 == typ && key.2 == forward && node.enabled)
            .map(|(key, _)| key.1)
            .min()
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TypeNode {
    pub enabled: bool,
    pub idx: usize,

    pub children: Vec<EndpointNode>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EndpointNode {
    pub enabled: bool,
    pub idx: usize,
    pub conn_idx: usize,

    pub children: Vec<SchemaRef>,
    pub rev_children: Vec<SchemaRef>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EdgeRef {
    pub node_idx: usize,
    pub conn_idx: usize,
    pub typ: usize,
    pub node_level: isize,
    pub depth: usize,
}

#[derive(Debug)]
pub struct MutationOptions {
    pub self_link_probability: f64,
    pub max_nodes: usize,
}

impl Default for MutationOptions {
    fn default() -> Self {
        Self {
            self_link_probability: 0.2,
            max_nodes: 50,
        }
    }
}

// Created at runtime and contains cached information useful for mutations.
#[derive(Debug)]
pub struct MutationContext {
    pub pspec: PSpec,
    pub opts: MutationOptions,
    pub cache: RefCell<SchemaCache>,
    rng: RefCell<rand::rngs::ThreadRng>,
}

impl MutationContext {
    pub fn new(pspec: PSpec) -> Self {
        Self {
            pspec,
            opts: MutationOptions::default(),
            cache: RefCell::new(SchemaCache::new()),
            rng: RefCell::new(rand::thread_rng()),
        }
    }

    pub fn save(&self, path: &Path) -> Result<(), Box<dyn std::error::Error>> {
        let mut file = File::create(path)?;
        let cache = self.cache.borrow().clone();
        serde_json::to_writer_pretty(&mut file, &cache)?;
        Ok(())
    }

    pub fn load(&self, path: &Path) -> Result<(), Box<dyn std::error::Error>> {
        let mut file = File::open(path)?;
        let cache: SchemaCache = serde_json::from_reader(&mut file)?;
        self.cache.replace(cache);
        Ok(())
    }

    pub fn compute(&self, max_depth: usize, bypass_validation: bool) {
        let mut missing_constructors = HashSet::new();
        let mut missing_destructors = HashSet::new();
        
        for typ in self.pspec.objects.iter() {
            if !(self.get_type_node_enabled(typ.idx, max_depth, true)) {
                missing_destructors.insert(typ.idx);
            }
            if !(self.get_type_node_enabled(typ.idx, max_depth, false)) {
                missing_constructors.insert(typ.idx);
            }
        }

        for x in self.pspec.objects.iter() {
            if missing_constructors.contains(&x.idx) {
                eprintln!("Missing constructor for type {}", x.name);
            }
            if missing_destructors.contains(&x.idx) {
                eprintln!("Missing destructor for type {}", x.name);
            }
        }

        let mut disabled_endpoints = HashSet::new();

        for endpoint in self.pspec.endpoints.iter() {
            for x in endpoint.inputs.iter() {
                if missing_constructors.contains(x) {
                    disabled_endpoints.insert(endpoint.idx);
                }
            }
        }

        for endpoint in self.pspec.endpoints.iter() {
            if !disabled_endpoints.contains(&endpoint.idx) {
                self.cache.borrow_mut().enabled_endpoints.push(endpoint.idx);
            }
        }

        let enabled = self.cache.borrow_mut().enabled_endpoints.len();
        let total = self.pspec.endpoints.len();

        eprintln!("Enabled endpoints: {}/{}", enabled, total);

        if !missing_constructors.is_empty() {
            if !bypass_validation {
                eprintln!("Missing constructors (bypass with --bypass-validation)");
                std::process::exit(1);
            }
        }

        if enabled == 0 {
            eprintln!("No enabled endpoints");
            std::process::exit(1);
        }
    }
    
    fn get_type_node_enabled(&self, idx: usize, depth: usize, forward: bool) -> bool {
        if depth == 0 {
            return false;
        }

        if !self.cache.borrow().nodes.contains_key(&(idx, depth, forward)) {
            let node = self.build_type_node(idx, depth, forward);
            self.cache.borrow_mut().nodes.insert((idx, depth, forward), node);
        }

        self.cache.borrow().nodes.get(&(idx, depth, forward)).unwrap().enabled
    }

    fn build_type_node(&self, idx: usize, depth: usize, forward: bool) -> TypeNode {
        let mut node = TypeNode {
            enabled: false,
            idx,
            children: Vec::new(),
        };

        for endpoint in self.pspec.endpoints.iter() {
            if forward {
                // We're adding nodes that consume the type. 
                for (conn_idx, val) in endpoint.inputs.iter().enumerate() {
                    if idx == *val {
                        let mut endpoint_node = EndpointNode {
                            enabled: true,
                            idx: endpoint.idx,
                            conn_idx,
                            children: Vec::new(),
                            rev_children: Vec::new(),
                        };

                        for (conn_in, t) in endpoint.inputs.iter().enumerate() {
                            if conn_idx == conn_in {
                                continue;
                            }
                            let e = self.get_type_node_enabled(*t, depth-1, false);
                            endpoint_node.rev_children.push((*t, depth-1, false));
                            endpoint_node.enabled &= e;
                        }

                        for (_, t) in endpoint.outputs.iter().enumerate() {
                            let e = self.get_type_node_enabled(*t, depth-1, true);
                            endpoint_node.children.push((*t, depth-1, true));

                            // Ignore children of a consumer node.
                            // endpoint_node.enabled &= e;
                        }

                        if endpoint_node.enabled {
                            node.children.push(endpoint_node);
                            node.enabled = true;
                        }
                    }
                }
            } else {
                // We're adding nodes that produce the type.
                for (conn_idx, val) in endpoint.outputs.iter().enumerate() {
                    if idx == *val {
                        let mut endpoint_node = EndpointNode {
                            enabled: true,
                            idx: endpoint.idx,
                            conn_idx,
                            children: Vec::new(),
                            rev_children: Vec::new(),
                        };

                        for (conn_out, t) in endpoint.outputs.iter().enumerate() {
                            if conn_idx == conn_out {
                                continue;
                            }
                            let e = self.get_type_node_enabled(*t, depth-1, true);
                            endpoint_node.rev_children.push((*t, depth-1, true));
                            
                            // Ignore rev_children of a producer node. (i.e. other destructors)
                            // endpoint_node.enabled &= e;
                        }

                        for (_, t) in endpoint.inputs.iter().enumerate() {
                            let e = self.get_type_node_enabled(*t, depth-1, false);
                            endpoint_node.children.push((*t, depth-1, false));
                            endpoint_node.enabled &= e;
                        }

                        if endpoint_node.enabled {
                            node.children.push(endpoint_node);
                            node.enabled = true;
                        }
                    }
                }
            }
        }

        node
    }

    // Given a graph with missing edges, complete the graph by adding nodes.
    fn complete(&self, graph: &mut Graph) {
        let mut free_inputs: Vec<EdgeRef> = Vec::new();
        let mut free_outputs: Vec<EdgeRef> = Vec::new();

        for (idx, node) in graph.nodes.iter().enumerate() {
            for (conn_idx, ref_type) in node.in_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        free_inputs.push(EdgeRef {
                            node_idx: idx,
                            conn_idx,
                            typ: self.pspec.endpoints[node.typ].inputs[conn_idx],
                            node_level: node.layer,
                            depth: usize::MAX,
                        });
                    },
                    NodeRefType::Connected(_) => {},
                }
            }

            for (conn_idx, ref_type) in node.out_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        free_outputs.push(EdgeRef {
                            node_idx: idx,
                            conn_idx,
                            typ: self.pspec.endpoints[node.typ].outputs[conn_idx],
                            node_level: node.layer,
                            depth: usize::MAX,
                        });
                    },
                    NodeRefType::Connected(_) => {},
                }
            }
        }

        free_inputs.shuffle(&mut *self.rng.borrow_mut());
        free_outputs.shuffle(&mut *self.rng.borrow_mut());

        while !free_inputs.is_empty() {
            let edge = free_inputs.pop().unwrap();
            self.complete_one(graph, &mut free_inputs, &mut free_outputs, edge);
        }
    }

    fn complete_one(&self, graph: &mut Graph, free_inputs: &mut Vec<EdgeRef>, free_outputs: &mut Vec<EdgeRef>, edge: EdgeRef) {
        // Check for an existing node that can be connected.
        if self.rng.borrow_mut().gen_bool(self.opts.self_link_probability) {
            let choice = {
                let linkable = free_outputs.iter().enumerate().filter(
                    |(_,out)|
                        out.typ == edge.typ &&
                        out.node_level < edge.node_level
                ).collect::<Vec<_>>();

                if !linkable.is_empty() {
                    let (idx, link) = linkable.choose(&mut *self.rng.borrow_mut()).unwrap().clone();

                    Some((idx, link.clone()))
                } else {
                    None
                }
            };

            if let Some((idx, link)) = choice {
                free_outputs.remove(idx);
                graph.link( link.node_idx, link.conn_idx, edge.node_idx, edge.conn_idx);

                return;
            }
        }

        // Otherwise, we need to sample from the schema.
        let cache = self.cache.borrow();

        // If depth is usize::MAX, use the smallest depth that is enabled.
        let depth = if edge.depth == usize::MAX {
            cache.smallest_depth(edge.typ, false).unwrap()
        } else {
            edge.depth
        };

        let typ_node = cache.get_type_node(edge.typ, depth, false).unwrap();
        self.apply_type_node(graph, free_inputs, free_outputs, edge, typ_node);
    }

    fn apply_type_node(&self, graph: &mut Graph, free_inputs: &mut Vec<EdgeRef>, free_outputs: &mut Vec<EdgeRef>, edge: EdgeRef, typ_node: &TypeNode) {
        // Sample a random endpoint.
        let endpoint = typ_node.children.choose(&mut *self.rng.borrow_mut());
        let endpoint = if let Some(endpoint) = endpoint {
            endpoint
        } else {
            println!("Type node: {:?}", typ_node);
            println!("Edge: {:?}", edge);
            println!("Cache: {:?}", self.cache.borrow());
            println!("Schema: {:?}", self.pspec);
            println!("Free inputs: {:?}", free_inputs);
            println!("Free outputs: {:?}", free_outputs);
            graph.pprint();
            panic!("Missing endpoint in type node");
        };

        let node = graph.add_node(
            &self.pspec,
            &mut *self.rng.borrow_mut(),
            endpoint.idx, 
            edge.node_level - 1, 
            self.pspec.endpoints[endpoint.idx].inputs.len(), 
            self.pspec.endpoints[endpoint.idx].outputs.len(), 
            self.pspec.endpoints[endpoint.idx].context_size.unwrap()
        );

        graph.link(node, endpoint.conn_idx, edge.node_idx, edge.conn_idx);

        // Add the new edges to the free list.
        for (i, (typ, depth, _)) in endpoint.children.iter().enumerate() {
            free_inputs.push(EdgeRef {
                node_idx: node,
                conn_idx: i,
                typ: *typ,
                node_level: graph.nodes[node].layer,
                depth: *depth,
            });
        }

        for (i, (typ, depth, _)) in endpoint.rev_children.iter().enumerate() {
            free_outputs.push(EdgeRef {
                node_idx: node,
                conn_idx: if i < endpoint.conn_idx { i } else { i + 1 }, // Skip the connected index
                typ: *typ,
                node_level: graph.nodes[node].layer,
                depth: *depth,
            });
        }
    }

    pub fn generate(&self) -> Graph {
        let mut graph = Graph {
            nodes: Vec::new(),
        };

        let cache = self.cache.borrow();
        let endpoint_idx = cache.enabled_endpoints.iter().choose(&mut *self.rng.borrow_mut()).unwrap();
        let endpoint = &self.pspec.endpoints[*endpoint_idx];
        let _ = graph.add_node(
            &self.pspec,
            &mut *self.rng.borrow_mut(),
            endpoint.idx, 
            0, 
            endpoint.inputs.len(), 
            endpoint.outputs.len(), endpoint.context_size.unwrap());

        self.complete(&mut graph);
        graph.sanitize();

        graph
    }

    pub fn generate_seeded(&self, idx: usize) -> Graph {
        let mut graph = Graph {
            nodes: Vec::new(),
        };
        
        let endpoint = &self.pspec.endpoints[idx];
        let _ = graph.add_node(
            &self.pspec,
            &mut *self.rng.borrow_mut(),
            endpoint.idx, 
            0, 
            endpoint.inputs.len(), 
            endpoint.outputs.len(), 
            endpoint.context_size.unwrap()
        );

        self.complete(&mut graph);
        graph.sanitize();

        graph
    }

    pub fn mutate_regenerate(&self, graph: &mut Graph) {
        *graph = self.generate();
    }

    pub fn mutate_random(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            return;
        }
        let choice = self.rng.borrow_mut().gen_range(0..2);
        match choice {
            0 => self.mutate_delete_one(graph),
            1 => self.mutate_isolate_component(graph),
            _ => unreachable!(),
        }
    }

    pub fn mutate_delete_one(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            return;
        }

        let node = self.rng.borrow_mut().gen_range(0..graph.nodes.len());

        graph.unlink_all(node);

        let keep = (0..graph.nodes.len()).filter(|&x| x != node).collect::<HashSet<_>>();
        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();
    }

    pub fn mutate_isolate_component(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            return;
        }

        let node = self.rng.borrow_mut().gen_range(0..graph.nodes.len());

        let mut keep = HashSet::new();
        graph.visit(node, &mut keep);
        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();
    }

    pub fn mutate_add_one(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            return;
        }

        // Pick a random node
        let node_idx = self.rng.borrow_mut().gen_range(0..graph.nodes.len());
        let node = &graph.nodes[node_idx];

        // Decide whether to pick from inputs or outputs
        let is_input = if !node.in_ref.is_empty() && !node.out_ref.is_empty() {
            self.rng.borrow_mut().gen_bool(0.5)
        } else {
            !node.in_ref.is_empty()
        };

        if (is_input && node.in_ref.is_empty()) || (!is_input && node.out_ref.is_empty()) {
            // No valid connections to modify, try regenerating
            self.mutate_regenerate(graph);
            return;
        }

        // Pick a random connection
        let conn_idx = if is_input {
            self.rng.borrow_mut().gen_range(0..node.in_ref.len())
        } else {
            self.rng.borrow_mut().gen_range(0..node.out_ref.len())
        };

        // Get the type we need to match
        let endpoint = &self.pspec.endpoints[node.typ];
        let typ = if is_input {
            endpoint.inputs[conn_idx]
        } else {
            endpoint.outputs[conn_idx]
        };

        // Remove the existing connection if there is one
        if is_input {
            if let NodeRefType::Connected(_) = &graph.nodes[node_idx].in_ref[conn_idx] {
                graph.unlink_backward(node_idx, conn_idx);
            }
        } else {
            if let NodeRefType::Connected(_) = &graph.nodes[node_idx].out_ref[conn_idx] {
                graph.unlink(node_idx, conn_idx);
            }
        }

        // Find enabled endpoints with a matching input/output type
        let cache = self.cache.borrow();
        let enabled_endpoint_indices = &cache.enabled_endpoints;
        
        let endpoints = if is_input {
            enabled_endpoint_indices.iter()
                .map(|&idx| &self.pspec.endpoints[idx])
                .filter(|e| e.outputs.contains(&typ))
                .collect::<Vec<_>>()
        } else {
            enabled_endpoint_indices.iter()
                .map(|&idx| &self.pspec.endpoints[idx])
                .filter(|e| e.inputs.contains(&typ))
                .collect::<Vec<_>>()
        };

        if endpoints.is_empty() {
            self.mutate_regenerate(graph);
            return;
        }

        // Pick a random endpoint
        let endpoint = endpoints.choose(&mut *self.rng.borrow_mut()).unwrap();

        // Add the new node
        let new_node = graph.add_node(
            &self.pspec,
            &mut *self.rng.borrow_mut(),
            endpoint.idx, 
            graph.nodes[node_idx].layer + if is_input { -1 } else { 1 }, 
            endpoint.inputs.len(), 
            endpoint.outputs.len(), 
            endpoint.context_size.unwrap()
        );

        // Link the new node
        if is_input {
            let new_conn_idx = endpoint.outputs.iter().enumerate().filter(|(_,x)| **x == typ).choose(&mut *self.rng.borrow_mut()).unwrap().0;
            graph.link(new_node, new_conn_idx, node_idx, conn_idx);
        } else {
            let new_conn_idx = endpoint.inputs.iter().enumerate().filter(|(_,x)| **x == typ).choose(&mut *self.rng.borrow_mut()).unwrap().0;
            graph.link(node_idx, conn_idx, new_node, new_conn_idx);
        }

        // Complete any remaining edges
        self.complete(graph);
        graph.sanitize();
    }

    pub fn mutate_crossover(&self, graph: &mut Graph, other: &Graph) {
        let mut other = other.clone();

        let shift_size = graph.nodes.len();
        other.apply_offset(shift_size);
        graph.nodes.extend(other.nodes);

        // Randomly keep 90% of the nodes
        let mut rng = rand::thread_rng();
        let mut keep = HashSet::new();
        for node in graph.nodes.iter() {
            if rng.gen_bool(0.9) {
                keep.insert(node.index);
            }
        }

        for node_idx in 0..graph.nodes.len() {
            if !keep.contains(&node_idx) {
                graph.unlink_all(node_idx);
            }
        }

        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();
    }
}
