use std::{cell::RefCell, collections::{HashMap, HashSet}};

use libc::free;
use rand::Rng;
use rand::seq::SliceRandom;

use crate::core::schema::Schema;

use super::graph::{Graph, NodeRefType};


// (type, depth, forward)
pub type SchemaRef = (usize, usize, bool);

#[derive(Debug)]
pub struct SchemaCache {
    pub nodes: HashMap<SchemaRef, TypeNode>,
}

impl SchemaCache {
    pub fn new() -> Self {
        Self {
            nodes: HashMap::new(),
        }
    }

    pub fn get_type_node(&self, typ: usize, depth: usize, forward: bool) -> Option<&TypeNode> {
        self.nodes.get(&(typ, depth, forward))
    }
}

#[derive(Debug)]
pub struct TypeNode {
    pub enabled: bool,
    pub idx: usize,

    pub children: Vec<EndpointNode>,
}

#[derive(Debug)]
pub struct EndpointNode {
    pub enabled: bool,
    pub idx: usize,
    pub conn_idx: usize,

    pub children: Vec<SchemaRef>,
    pub rev_children: Vec<SchemaRef>,
}

#[derive(Debug, Clone)]
pub struct EdgeRef {
    pub node_idx: usize,
    pub conn_idx: usize,
    pub forward: bool,
    pub typ: usize,
    pub node_level: isize,
    pub depth: usize,
}

#[derive(Debug)]
pub struct MutationOptions {
    pub self_link_probability: f64,
}

impl Default for MutationOptions {
    fn default() -> Self {
        Self {
            self_link_probability: 0.2,
        }
    }
}

// Created at runtime and contains cached information useful for mutations.
#[derive(Debug)]
pub struct MutationContext {
    schema: Schema,
    opts: MutationOptions,
    cache: RefCell<SchemaCache>,
    rng: RefCell<rand::rngs::ThreadRng>,
}

impl MutationContext {
    pub fn new(schema: Schema) -> Self {
        Self {
            schema,
            opts: MutationOptions::default(),
            cache: RefCell::new(SchemaCache::new()),
            rng: RefCell::new(rand::thread_rng()),
        }
    }

    pub fn compute(&self, max_depth: usize) {
        for typ in self.schema.types.iter() {
            self.get_type_node_enabled(typ.idx, max_depth, true);
            self.get_type_node_enabled(typ.idx, max_depth, false);
        }

        println!("{:?}", self.cache.borrow().nodes);
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

        for endpoint in self.schema.endpoints.iter() {
            if forward {
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
                            endpoint_node.enabled &= e;
                        }

                        if endpoint_node.enabled {
                            node.children.push(endpoint_node);
                            node.enabled = true;
                        }
                    }
                }
            } else {
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
                            endpoint_node.enabled &= e;
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
        let mut free_edges: Vec<EdgeRef> = Vec::new();

        for (idx, node) in graph.nodes.iter().enumerate() {
            for (conn_idx, ref_type) in node.in_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        free_edges.push(EdgeRef {
                            node_idx: idx,
                            conn_idx,
                            forward: false,
                            typ: self.schema.endpoints[node.typ].inputs[conn_idx],
                            node_level: node.layer,
                            depth: 3,
                        });
                    },
                    NodeRefType::Connected(_) => {},
                }
            }

            for (conn_idx, ref_type) in node.out_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        free_edges.push(EdgeRef {
                            node_idx: idx,
                            conn_idx,
                            forward: true,
                            typ: self.schema.endpoints[node.typ].outputs[conn_idx],
                            node_level: node.layer,
                            depth: 3,
                        });
                    },
                    NodeRefType::Connected(_) => {},
                }
            }
        }

        free_edges.shuffle(&mut *self.rng.borrow_mut());

        while !free_edges.is_empty() {
            let edge = free_edges.pop().unwrap();
            self.complete_one(graph, &mut free_edges, edge);
        }
    }

    fn complete_one(&self, graph: &mut Graph, free_edges: &mut Vec<EdgeRef>, edge: EdgeRef) {
        // Check for an existing node that can be connected.
        if self.rng.borrow_mut().gen_bool(self.opts.self_link_probability) {
            let choice = {
                let linkable = free_edges.iter().enumerate().filter(
                    |(i,x)|
                        x.typ == edge.typ &&
                        x.forward != edge.forward &&
                        if edge.forward { x.node_level > edge.node_level } else { x.node_level < edge.node_level }
                ).collect::<Vec<_>>();

                if !linkable.is_empty() {
                    let (idx, link) = linkable.choose(&mut *self.rng.borrow_mut()).unwrap().clone();

                    Some((idx, link.clone()))
                } else {
                    None
                }
            };

            if let Some((idx, link)) = choice {
                free_edges.remove(idx);

                if edge.forward {
                    graph.link(edge.node_idx, edge.conn_idx, link.node_idx, link.conn_idx);
                } else {
                    graph.link(link.node_idx, link.conn_idx, edge.node_idx, edge.conn_idx);
                }

                return;
            }
        }

        // Otherwise, we need to sample from the schema.
        let cache = self.cache.borrow();
        let typ_node = cache.get_type_node(edge.typ, edge.depth, edge.forward).unwrap();
        self.apply_type_node(graph, free_edges, edge, typ_node);
    }

    fn apply_type_node(&self, graph: &mut Graph, free_edges: &mut Vec<EdgeRef>, edge: EdgeRef, typ_node: &TypeNode) {
        // Sample a random endpoint.
        let endpoint = typ_node.children.choose(&mut *self.rng.borrow_mut()).unwrap();

        let node = graph.add_node(
            endpoint.idx, 
            edge.node_level + if edge.forward { 1 } else { -1 }, 
            self.schema.endpoints[endpoint.idx].inputs.len(), 
            self.schema.endpoints[endpoint.idx].outputs.len(), 
            self.schema.endpoints[endpoint.idx].context_size
        );

        // Link the edge.
        if edge.forward {
            graph.link(edge.node_idx, edge.conn_idx, node, endpoint.conn_idx);
        } else {
            graph.link(node, endpoint.conn_idx, edge.node_idx, edge.conn_idx);
        }

        // Add the new edges to the free list.
        for (i, (typ, depth, fwd)) in endpoint.children.iter().enumerate() {
            free_edges.push(EdgeRef {
                node_idx: node,
                conn_idx: i,
                forward: *fwd,
                typ: *typ,
                node_level: graph.nodes[node].layer,
                depth: *depth,
            });
        }

        for (i, (typ, depth, fwd)) in endpoint.rev_children.iter().enumerate() {
            free_edges.push(EdgeRef {
                node_idx: node,
                conn_idx: if i < endpoint.conn_idx { i } else { i + 1 }, // Skip the connected index
                forward: *fwd,
                typ: *typ,
                node_level: graph.nodes[node].layer,
                depth: *depth,
            });
        }
    }

    pub fn mutate_random(&self, graph: &mut Graph) {
        let choice = self.rng.borrow_mut().gen_range(0..2);
        match choice {
            0 => self.mutate_delete_one(graph),
            1 => self.mutate_isolate_component(graph),
            _ => unreachable!(),
        }
    }

    pub fn mutate_delete_one(&self, graph: &mut Graph) {
        let node = self.rng.borrow_mut().gen_range(0..graph.nodes.len());

        graph.unlink_all(node);

        let keep = (0..graph.nodes.len()).filter(|&x| x != node).collect::<HashSet<_>>();
        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();
    }

    pub fn mutate_isolate_component(&self, graph: &mut Graph) {
        let node = self.rng.borrow_mut().gen_range(0..graph.nodes.len());

        let mut keep = HashSet::new();
        graph.visit(node, &mut keep);
        graph.collapse(&keep);

        self.complete(graph);
        graph.sanitize();
    }
}


#[cfg(test)]
mod tests {
    use crate::core::graph::Node;

    use super::*;

    fn setup_ctx() -> MutationContext {
        let raw_schema = r#"
{
    "types": [
        {
            "name": "Foo",
            "idx": 0
        }
    ],
    "endpoints": [
        {
            "name": "Foo()",
            "idx": 0,
            "inputs": [],
            "outputs": [0],
            "context_size": 0
        },
        {
            "name": "Foo::write(char v)",
            "idx": 1,
            "inputs": [0],
            "outputs": [0],
            "context_size": 1
        },
        {
            "name": "Foo::check()",
            "idx": 2,
            "inputs": [0],
            "outputs": [0],
            "context_size": 0
        },
        {
            "name": "~Foo()",
            "idx": 3,
            "inputs": [0],
            "outputs": [],
            "context_size": 0
        }
    ]
}
        "#;

        let schema: Schema = serde_json::from_str(raw_schema).unwrap();
        let ctx = MutationContext::new(schema);
        ctx.compute(3);
        ctx
    }

    #[test]
    fn test_complete() {
        let mut ctx = setup_ctx();

        let mut graph = Graph {
            nodes: vec![
                Node {
                    index: 0,
                    typ: 1,
                    layer: 0,
                    in_ref: vec![NodeRefType::Disconnected; 1],
                    out_ref: vec![NodeRefType::Disconnected; 1],
                    context: vec![0],
                }
            ]
        };

        ctx.complete(&mut graph);

        graph.pprint();
        graph.validate(&ctx.schema);

        ctx.mutate_delete_one(&mut graph);

        graph.pprint();
        graph.validate(&ctx.schema);
    }
}