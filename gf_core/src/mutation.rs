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
    /// When true, enable extra graph validation and verbose debugging output
    /// during mutation operations.
    pub extra_verbose: bool,
    /// When true, allow graph structure validation to run where enabled.
    /// Typically controlled by the `GF_VALIDATE_GRAPHS` env var.
    pub validate_graphs: bool,
    /// Probability (0.0–1.0) of attempting cross-pollination for hint-backed
    /// buffers during data-level mutation. When a cross-pollination attempt
    /// fails to find a compatible donor, the mutator falls back to the
    /// standard in-place mutation.
    pub cross_pollination_probability: f64,
}

impl Default for MutationOptions {
    fn default() -> Self {
        // Evaluate once so we don't repeatedly hit the environment in hot paths.
        let validate_graphs = std::env::var("GF_VALIDATE_GRAPHS").is_ok();
        // Extra verbosity can be enabled independently, but we also treat
        // `GF_VALIDATE_GRAPHS` as implying extra verbosity for backwards
        // compatibility with existing workflows.
        let extra_verbose_env = std::env::var("GF_EXTRA_VERBOSE").is_ok();
        let extra_verbose = extra_verbose_env || validate_graphs;

        // Cross-pollination probability can be tuned via GF_CROSS_POLL_PROB in
        // the environment. Values outside [0.0, 1.0] are clamped.
        let cross_pollination_probability = std::env::var("GF_CROSS_POLL_PROB")
            .ok()
            .and_then(|s| s.parse::<f64>().ok())
            .map(|p| p.clamp(0.0, 1.0))
            .unwrap_or(0.3);

        Self {
            self_link_probability: 0.2,
            max_nodes: 50,
            extra_verbose,
            validate_graphs,
            cross_pollination_probability,
        }
    }
}

// Created at runtime and contains cached information useful for mutations.
#[derive(Debug)]
pub struct MutationContext {
    pub pspec: PSpec,
    pub opts: MutationOptions,
    pub cache: RefCell<SchemaCache>,
    pub raw_inputs: RefCell<Vec<String>>,
    pub rng: RefCell<rand::rngs::ThreadRng>,
}

impl MutationContext {
    /// Debug-only helper: validate graph structure against the pspec, with a label.
    /// Controlled by the `GF_VALIDATE_GRAPHS` env var to avoid slowing down normal fuzzing.
    fn validate_graph_debug(&self, where_label: &str, graph: &Graph) {
        // Only perform the extra validation when explicitly requested. We also
        // avoid repeated env checks by relying on the cached options.
        if !self.opts.extra_verbose {
            return;
        }

        eprintln!(
            "[gf_core::mutation::validate_graph_debug] at {}: nodes_len={}, validate_graphs={}",
            where_label,
            graph.nodes.len(),
            self.opts.validate_graphs,
        );

        if self.opts.validate_graphs {
            graph.validate(&self.pspec);
        }
    }

    pub fn new(pspec: PSpec) -> Self {
        Self {
            pspec,
            opts: MutationOptions::default(),
            cache: RefCell::new(SchemaCache::new()),
            raw_inputs: RefCell::new(Vec::new()),
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
        // Analyze which types are constructible at the given depth
        let mut missing_constructors = HashSet::new();
        
        for typ in self.pspec.objects.iter() {
            if !(self.get_type_node_enabled(typ.idx, max_depth, false)) {
                missing_constructors.insert(typ.idx);
            }
        }

        // Analyze disabled endpoints and their dependencies
        let mut disabled_endpoints = Vec::new();
        let mut enabled_endpoints = Vec::new();

        for endpoint in self.pspec.endpoints.iter() {
            let mut missing_dependencies = Vec::new();
            
            // Check each input type to see if it's constructible
            for input_type in endpoint.inputs.iter() {
                if missing_constructors.contains(input_type) {
                    let type_name = &self.pspec.objects[*input_type].name;
                    missing_dependencies.push(type_name.clone());
                }
            }
            
            if missing_dependencies.is_empty() {
                enabled_endpoints.push(endpoint.idx);
            } else {
                disabled_endpoints.push((endpoint.idx, missing_dependencies));
            }
        }

        // Store enabled endpoints in cache
        self.cache.borrow_mut().enabled_endpoints = enabled_endpoints.clone();

        let enabled_count = enabled_endpoints.len();
        let total_count = self.pspec.endpoints.len();
        let disabled_count = disabled_endpoints.len();

        eprintln!("Endpoint Analysis (depth={}):", max_depth);
        eprintln!("  Enabled endpoints: {}/{}", enabled_count, total_count);
        eprintln!("  Disabled endpoints: {}/{}", disabled_count, total_count);
        eprintln!();

        // Show disabled endpoints with their dependencies
        if !disabled_endpoints.is_empty() {
            eprintln!("Disabled endpoints:");
            for (endpoint_idx, missing_deps) in disabled_endpoints.iter() {
                let endpoint = &self.pspec.endpoints[*endpoint_idx];
                eprintln!("  • {}: Missing constructors for: {}", 
                    endpoint.name, 
                    missing_deps.join(", ")
                );
            }
            eprintln!();
        }

        // Validation logic
        if !disabled_endpoints.is_empty() {
            if !bypass_validation {
                eprintln!("Disabled endpoints (bypass with --bypass-validation)");
                std::process::exit(1);
            }
        }

        if enabled_count == 0 {
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
                            endpoint_node.children.push((*t, depth-1, true));

                            // Ignore children of a consumer node.
                            // let e = self.get_type_node_enabled(*t, depth-1, true);
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
                            endpoint_node.rev_children.push((*t, depth-1, true));
                            
                            // Ignore rev_children of a producer node. (i.e. other destructors)
                            // let e = self.get_type_node_enabled(*t, depth-1, true);
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
            &self,
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
        let mut graph = Graph::new();

        let cache = self.cache.borrow();
        let endpoint_idx = cache.enabled_endpoints.iter().choose(&mut *self.rng.borrow_mut()).unwrap();
        let endpoint = &self.pspec.endpoints[*endpoint_idx];
        let _ = graph.add_node(
            &self,
            endpoint.idx, 
            0, 
            endpoint.inputs.len(), 
            endpoint.outputs.len(), 
            endpoint.context_size.unwrap()
        );

        self.complete(&mut graph);
        graph.sanitize();

        self.validate_graph_debug("generate:post", &graph);

        graph
    }

    pub fn generate_seeded(&self, idx: usize) -> Graph {
        let mut graph = Graph::new();
        
        let endpoint = &self.pspec.endpoints[idx];
        let _ = graph.add_node(
            &self,
            endpoint.idx, 
            0, 
            endpoint.inputs.len(), 
            endpoint.outputs.len(), 
            endpoint.context_size.unwrap()
        );

        self.complete(&mut graph);
        graph.sanitize();

        self.validate_graph_debug("generate_seeded:post", &graph);

        graph
    }

    pub fn mutate_regenerate(&self, graph: &mut Graph) {
        *graph = self.generate();
        self.validate_graph_debug("mutate_regenerate:post", graph);
    }

    pub fn mutate_random(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            self.validate_graph_debug("mutate_random:post_empty", graph);
            return;
        }
        let choice = self.rng.borrow_mut().gen_range(0..2);
        match choice {
            0 => {
                self.mutate_delete_one(graph);
                self.validate_graph_debug("mutate_random:post_delete_one", graph);
            }
            1 => {
                self.mutate_isolate_component(graph);
                self.validate_graph_debug("mutate_random:post_isolate_component", graph);
            }
            _ => unreachable!(),
        }
    }

    pub fn mutate_delete_one(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            self.validate_graph_debug("mutate_delete_one:post_empty", graph);
            return;
        }

        let node = self.rng.borrow_mut().gen_range(0..graph.nodes.len());

        graph.unlink_all(node);

        let keep = (0..graph.nodes.len()).filter(|&x| x != node).collect::<HashSet<_>>();
        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();

        self.validate_graph_debug("mutate_delete_one:post", graph);
    }

    pub fn mutate_isolate_component(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            self.validate_graph_debug("mutate_isolate_component:post_empty", graph);
            return;
        }

        let node = self.rng.borrow_mut().gen_range(0..graph.nodes.len());

        let mut keep = HashSet::new();
        graph.visit(node, &mut keep);
        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();

        self.validate_graph_debug("mutate_isolate_component:post", graph);
    }

    pub fn mutate_add_one(&self, graph: &mut Graph) {
        if graph.nodes.len() == 0 {
            *graph = self.generate();
            self.validate_graph_debug("mutate_add_one:post_empty", graph);
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
            self.validate_graph_debug("mutate_add_one:post_regenerate", graph);
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
            self.validate_graph_debug("mutate_add_one:post_regenerate_no_endpoints", graph);
            return;
        }

        // Pick a random endpoint
        let endpoint = endpoints.choose(&mut *self.rng.borrow_mut()).unwrap();

        // Add the new node
        let new_node = graph.add_node(
            &self,
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

        // self.validate_graph_debug("mutate_add_one:post", graph);
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

        self.validate_graph_debug("mutate_crossover:post", graph);
    }

    /// Trim or repair the graph around the last executed region, using the
    /// bail information when available.
    ///
    /// `bail_node_idx` is the index of the node where execution first bailed,
    /// if any. `executed_prefix_len` is the number of nodes that executed in
    /// invocation order, including the bail node if present.
    pub fn mutate_frontier_trim_and_repair(
        &self,
        graph: &mut Graph,
        bail_node_idx: Option<usize>,
        executed_prefix_len: usize,
    ) {
        if self.opts.extra_verbose {
            eprintln!(
                "[gf_core::mutation::mutate_frontier_trim_and_repair] start: nodes_len={}, bail_node_idx={:?}, executed_prefix_len={}",
                graph.nodes.len(),
                bail_node_idx,
                executed_prefix_len
            );
        }

        if graph.nodes.is_empty() {
            *graph = self.generate();
            return;
        }

        let order = graph.topological_sort();
        if order.is_empty() {
            *graph = self.generate();
            return;
        }

        // Clamp prefix length to the current graph size.
        let prefix_len = executed_prefix_len.min(order.len());

        // If we have no information, fall back to a lighter-weight mutation.
        if prefix_len == 0 && bail_node_idx.is_none() {
            self.mutate_random(graph);
            return;
        }

        let mut rng = self.rng.borrow_mut();
        let use_strict_prefix = rng.gen_bool(0.5);
        drop(rng);

        if use_strict_prefix {
            // Option B: keep only the strict prefix before the failing node (if
            // known), or the executed prefix otherwise, then re-complete.
            let mut keep = HashSet::new();

            if let Some(bail_idx) = bail_node_idx {
                // Keep nodes up to but excluding the bail node in invocation
                // order.
                let bail_in_order = order.iter().any(|idx| *idx == bail_idx);
                if self.opts.extra_verbose {
                    eprintln!(
                        "[gf_core::mutation::mutate_frontier_trim_and_repair] strict_prefix: bail_idx={}, bail_in_order={}",
                        bail_idx,
                        bail_in_order
                    );
                }

                for idx in order.iter() {
                    if *idx == bail_idx {
                        break;
                    }
                    keep.insert(*idx);
                }
            } else {
                for idx in order.iter().take(prefix_len) {
                    keep.insert(*idx);
                }
            }
            if self.opts.extra_verbose {
                eprintln!(
                    "[gf_core::mutation::mutate_frontier_trim_and_repair] strict_prefix: order_len={}, prefix_len={}, keep_len={}",
                    order.len(),
                    prefix_len,
                    keep.len()
                );
            }

            if keep.is_empty() {
                // If we somehow ended up with nothing to keep, just regenerate.
                *graph = self.generate();
                return;
            }

            graph.collapse(&keep);
            self.complete(graph);
            graph.sanitize();

            self.validate_graph_debug("mutate_frontier_trim_and_repair:post_strict_prefix", graph);
        } else {
            // Option A: keep the failing node but sever its outgoing edges so
            // that new consumer chains can be attached on the next completion.
            if self.opts.extra_verbose {
                eprintln!(
                    "[gf_core::mutation::mutate_frontier_trim_and_repair] sever-outgoing: bail_node_idx={:?}",
                    bail_node_idx
                );
            }

            if let Some(bail_idx) = bail_node_idx {
                if bail_idx < graph.nodes.len() {
                    if self.opts.extra_verbose {
                        eprintln!(
                            "[gf_core::mutation::mutate_frontier_trim_and_repair] sever-outgoing: bail_idx={}, out_ref_len={}",
                            bail_idx,
                            graph.nodes[bail_idx].out_ref.len()
                        );
                    }
                    let out_len = graph.nodes[bail_idx].out_ref.len();
                    for conn in 0..out_len {
                        graph.unlink(bail_idx, conn);
                    }
                }
            }

            self.complete(graph);
            graph.sanitize();

            self.validate_graph_debug("mutate_frontier_trim_and_repair:post_sever_outgoing", graph);
        }
    }

    /// Extend the graph from the frontier of the executed prefix by inserting
    /// a new node attached near the last executed node.
    pub fn mutate_frontier_extend(&self, graph: &mut Graph, executed_prefix_len: usize) {
        if graph.nodes.is_empty() {
            *graph = self.generate();
            self.validate_graph_debug("mutate_frontier_extend:post_empty", graph);
            return;
        }

        let order = graph.topological_sort();
        if order.is_empty() {
            *graph = self.generate();
            self.validate_graph_debug("mutate_frontier_extend:post_empty_order", graph);
            return;
        }

        let prefix_len = executed_prefix_len.min(order.len());

        // Choose a frontier node: the last executed node if we have a prefix,
        // otherwise a random node.
        let frontier_node_idx = if prefix_len > 0 {
            order[prefix_len - 1]
        } else {
            self.rng.borrow_mut().gen_range(0..graph.nodes.len())
        };

        // Reuse the logic from mutate_add_one, but target the chosen frontier
        // node.
        let node = &graph.nodes[frontier_node_idx];

        // Decide whether to pick from inputs or outputs.
        let is_input = if !node.in_ref.is_empty() && !node.out_ref.is_empty() {
            self.rng.borrow_mut().gen_bool(0.5)
        } else {
            !node.in_ref.is_empty()
        };

        if (is_input && node.in_ref.is_empty()) || (!is_input && node.out_ref.is_empty()) {
            // No valid connections to modify, fall back to a random add-one.
            self.mutate_add_one(graph);
            self.validate_graph_debug("mutate_frontier_extend:post_fallback_add_one", graph);
            return;
        }

        // Pick a random connection on the chosen side.
        let conn_idx = if is_input {
            self.rng.borrow_mut().gen_range(0..node.in_ref.len())
        } else {
            self.rng.borrow_mut().gen_range(0..node.out_ref.len())
        };

        // Get the type we need to match.
        let endpoint = &self.pspec.endpoints[node.typ];
        let typ = if is_input {
            endpoint.inputs[conn_idx]
        } else {
            endpoint.outputs[conn_idx]
        };

        // Remove the existing connection if there is one.
        if is_input {
            if let NodeRefType::Connected(_) = &graph.nodes[frontier_node_idx].in_ref[conn_idx] {
                graph.unlink_backward(frontier_node_idx, conn_idx);
            }
        } else if let NodeRefType::Connected(_) =
            &graph.nodes[frontier_node_idx].out_ref[conn_idx]
        {
            graph.unlink(frontier_node_idx, conn_idx);
        }

        // Find enabled endpoints with a matching input/output type.
        let cache = self.cache.borrow();
        let enabled_endpoint_indices = &cache.enabled_endpoints;

        let endpoints = if is_input {
            enabled_endpoint_indices
                .iter()
                .map(|&idx| &self.pspec.endpoints[idx])
                .filter(|e| e.outputs.contains(&typ))
                .collect::<Vec<_>>()
        } else {
            enabled_endpoint_indices
                .iter()
                .map(|&idx| &self.pspec.endpoints[idx])
                .filter(|e| e.inputs.contains(&typ))
                .collect::<Vec<_>>()
        };

        if endpoints.is_empty() {
            self.mutate_regenerate(graph);
            return;
        }

        // Pick a random endpoint.
        let endpoint = endpoints
            .choose(&mut *self.rng.borrow_mut())
            .unwrap();

        // Add the new node.
        let new_node = graph.add_node(
            &self,
            endpoint.idx,
            graph.nodes[frontier_node_idx].layer + if is_input { -1 } else { 1 },
            endpoint.inputs.len(),
            endpoint.outputs.len(),
            endpoint.context_size.unwrap(),
        );

        // Link the new node.
        if is_input {
            let new_conn_idx = endpoint
                .outputs
                .iter()
                .enumerate()
                .filter(|(_, x)| **x == typ)
                .choose(&mut *self.rng.borrow_mut())
                .unwrap()
                .0;
            graph.link(new_node, new_conn_idx, frontier_node_idx, conn_idx);
        } else {
            let new_conn_idx = endpoint
                .inputs
                .iter()
                .enumerate()
                .filter(|(_, x)| **x == typ)
                .choose(&mut *self.rng.borrow_mut())
                .unwrap()
                .0;
            graph.link(frontier_node_idx, conn_idx, new_node, new_conn_idx);
        }

        // Complete any remaining edges and sanitize.
        self.complete(graph);
        graph.sanitize();

        self.validate_graph_debug("mutate_frontier_extend:post", graph);
    }
}
