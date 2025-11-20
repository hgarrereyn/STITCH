use std::{collections::HashSet, num::ParseIntError};

use colored::Colorize;
use rand::Rng;
use serde::{Deserialize, Serialize};

use crate::{mutation::MutationContext, pspec::PSpec};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct NodeRef {
    pub node_idx: usize,
    pub conn_idx: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum NodeRefType {
    Disconnected,
    Connected(NodeRef),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Node {
    // Represents the index of the node in the graph
    pub index: usize,

    // Points to the corresponding shim function
    pub typ: usize,

    // Nodes can only have forward connections to other nodes with a higher layer
    pub layer: isize,

    pub in_ref: Vec<NodeRefType>,

    pub out_ref: Vec<NodeRefType>,

    // Embedded context data
    pub context: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Graph {
    pub nodes: Vec<Node>,

    // Stores the invocation order (topological sort) for visualization
    // This is optional for backward compatibility with existing testcases
    #[serde(skip_serializing_if = "Option::is_none")]
    pub invocation_order: Option<Vec<usize>>,
}

impl Graph {
    pub fn pprint(&self) {
        // Sort nodes by layer
        let ordered = self.topological_sort();

        println!("Graph {{");

        for idx in ordered {
            let node = &self.nodes[idx];
            let index = format!("{}", node.index).yellow();
            let typ = format!("{}", node.typ).blue();
            let layer = format!("{}", node.layer).green();
            let in_ref = node
                .in_ref
                .iter()
                .map(|x| match x {
                    NodeRefType::Disconnected => format!("{}", "D".red()),
                    NodeRefType::Connected(ref y) => {
                        format!("{}", format!("{}:{}", y.node_idx, y.conn_idx).blue())
                    }
                })
                .collect::<Vec<String>>()
                .join(" ");
            let out_ref = node
                .out_ref
                .iter()
                .map(|x| match x {
                    NodeRefType::Disconnected => format!("{}", "D".red()),
                    NodeRefType::Connected(ref y) => {
                        format!("{}", format!("{}:{}", y.node_idx, y.conn_idx).blue())
                    }
                })
                .collect::<Vec<String>>()
                .join(" ");
            let context = format!("{:?}", node.context).yellow();

            println!(
                "    {} t:{} {} in[{}] out[{}] {}",
                index, typ, layer, in_ref, out_ref, context
            );
        }

        println!("}}");
    }

    pub fn topological_sort(&self) -> Vec<usize> {
        let mut ordered = (0..self.nodes.len()).collect::<Vec<usize>>();
        ordered.sort_by_key(|&x| self.nodes[x].layer);
        ordered
    }

    /// Ensures that the invocation order is computed and stored in the graph.
    /// This should be called before serializing the graph to disk.
    pub fn ensure_invocation_order(&mut self) {
        if self.invocation_order.is_none()
            || self.invocation_order.as_ref().unwrap().len() != self.nodes.len()
        {
            self.invocation_order = Some(self.topological_sort());
        }
    }

    /// Gets the invocation order, using the cached version if available, otherwise computing it.
    pub fn get_invocation_order(&self) -> Vec<usize> {
        self.invocation_order
            .clone()
            .unwrap_or_else(|| self.topological_sort())
    }

    pub fn validate(&self, spec: &PSpec) {
        for (idx, node) in self.nodes.iter().enumerate() {
            assert!(node.index == idx, "Node index mismatch at {}", idx);
            assert!(
                node.typ < spec.endpoints.len(),
                "Node type out of bounds at {}",
                idx
            );

            // Ensure node has the correct number of input references
            assert!(
                node.in_ref.len() == spec.endpoints[node.typ].inputs.len(),
                "Node input reference count mismatch at {} (has {}, expected {})",
                idx,
                node.in_ref.len(),
                spec.endpoints[node.typ].inputs.len()
            );

            // Ensure node has the correct number of output references
            assert!(
                node.out_ref.len() == spec.endpoints[node.typ].outputs.len(),
                "Node output reference count mismatch at {} (has {}, expected {})",
                idx,
                node.out_ref.len(),
                spec.endpoints[node.typ].outputs.len()
            );

            // Ensure the context size matches the schema
            assert!(
                node.context.len() == spec.endpoints[node.typ].context_size.unwrap(),
                "Node context size mismatch at {} (has {}, expected {})",
                idx,
                node.context.len(),
                spec.endpoints[node.typ].context_size.unwrap()
            );

            // Ensure inputs and outputs are connected
            for (i, ref_type) in node.in_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        assert!(
                            false,
                            "Node input connection is disconnected at {} (conn {})",
                            idx, i
                        );
                    }
                    NodeRefType::Connected(ref node_ref) => {
                        assert!(
                            node_ref.node_idx < self.nodes.len(),
                            "Node input connection idx out of bounds at {} (conn {})",
                            idx,
                            i
                        );
                        assert!(
                            node_ref.conn_idx < self.nodes[node_ref.node_idx].out_ref.len(),
                            "Node input connection conn out of bounds at {} (conn {})",
                            idx,
                            i
                        );

                        // Check layer
                        assert!(
                            node.layer > self.nodes[node_ref.node_idx].layer,
                            "Node layer mismatch at {} (conn {})",
                            idx,
                            i
                        );

                        // Ensure the connected node has a corresponding output reference
                        assert!(
                            self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx]
                                == NodeRefType::Connected(NodeRef {
                                    node_idx: idx,
                                    conn_idx: i
                                }),
                            "Node input connection mismatch at {} (conn {})",
                            idx,
                            i
                        );
                    }
                }
            }

            for (i, ref_type) in node.out_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        assert!(
                            false,
                            "Node output connection is disconnected at {} (conn {})",
                            idx, i
                        );
                    }
                    NodeRefType::Connected(ref node_ref) => {
                        assert!(
                            node_ref.node_idx < self.nodes.len(),
                            "Node output connection idx out of bounds at {} (conn {})",
                            idx,
                            i
                        );
                        assert!(
                            node_ref.conn_idx < self.nodes[node_ref.node_idx].in_ref.len(),
                            "Node output connection conn out of bounds at {} (conn {})",
                            idx,
                            i
                        );

                        // Check layer
                        assert!(
                            node.layer < self.nodes[node_ref.node_idx].layer,
                            "Node layer mismatch at {} (conn {})",
                            idx,
                            i
                        );

                        // Ensure the connected node has a corresponding input reference
                        assert!(
                            self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx]
                                == NodeRefType::Connected(NodeRef {
                                    node_idx: idx,
                                    conn_idx: i
                                }),
                            "Node output connection mismatch at {} (conn {})",
                            idx,
                            i
                        );
                    }
                }
            }
        }
    }

    pub fn add_node(
        &mut self,
        ctx: &MutationContext,
        typ: usize,
        layer: isize,
        in_size: usize,
        out_size: usize,
        context_size: usize,
    ) -> usize {
        let idx = self.nodes.len();

        // Initialize buffer arguments with hints if possible
        let mut context = vec![0; context_size];

        let has_hints = ctx.pspec.endpoints[typ].hints.len() > 0;
        let has_raw_inputs = ctx.raw_inputs.borrow().len() > 0;

        if has_hints || has_raw_inputs {
            // Iterate over buffers in the node and fill them with hints or raw inputs
            for (hint_arg, max_hint_size) in ctx.pspec.endpoints[typ]
                .hint_args
                .iter()
                .zip(ctx.pspec.endpoints[typ].hint_args_sizes.iter())
            {
                // Hints are of the type: [size (u32)][data (u8[])]
                // In the pspec, hint_arg points to the size, and max_hint_size specifies the backing size of the data section
                // We need to update the size argument to a valid size and then fill the backing buffer with the hint data

                // Select either a random hint or a random raw input
                let (will_use_hints, will_use_raw_inputs) = match (has_hints, has_raw_inputs) {
                    (true, true) => {
                        let p = ctx.rng.borrow_mut().gen_bool(0.5);
                        (p, !p)
                    }
                    (true, false) => (true, false),
                    (false, true) => (false, true),
                    (false, false) => (false, false),
                };

                // Retrieve random data for the buffer
                let mut data = if will_use_hints {
                    let hint_idx = ctx
                        .rng
                        .borrow_mut()
                        .gen_range(0..ctx.pspec.endpoints[typ].hints.len());
                    let hint = ctx.pspec.endpoints[typ].hints[hint_idx].clone();
                    decode_hex(&hint).unwrap()
                } else {
                    let raw_input_idx = ctx
                        .rng
                        .borrow_mut()
                        .gen_range(0..ctx.raw_inputs.borrow().len());
                    ctx.raw_inputs.borrow()[raw_input_idx]
                        .clone()
                        .as_bytes()
                        .to_vec()
                };

                // Lookup the hint arg offset
                let off = ctx.pspec.endpoints[typ].arg_offsets[*hint_arg] as usize;

                // Truncate the data to the max buffer arg size
                data.truncate(*max_hint_size);

                // Set the size of the buffer
                context[off..off + 4].copy_from_slice(&(data.len() as u32).to_le_bytes());

                // Set the data of the buffer
                context[off + 4..off + 4 + data.len()].copy_from_slice(&data);
            }
        }

        self.nodes.push(Node {
            index: idx,
            typ,
            layer,
            in_ref: vec![NodeRefType::Disconnected; in_size],
            out_ref: vec![NodeRefType::Disconnected; out_size],
            context,
        });

        idx
    }

    // Unlink node A from its forward connection
    pub fn unlink(&mut self, node_a: usize, conn_a: usize) {
        let node_ref = match self.nodes[node_a].out_ref[conn_a] {
            NodeRefType::Connected(ref node_ref) => node_ref.clone(),
            NodeRefType::Disconnected => return,
        };

        self.nodes[node_a].out_ref[conn_a] = NodeRefType::Disconnected;
        self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx] = NodeRefType::Disconnected;
        self.invocation_order = None;
    }

    // Unlink node A from its backward connection
    pub fn unlink_backward(&mut self, node_a: usize, conn_a: usize) {
        let node_ref = match self.nodes[node_a].in_ref[conn_a] {
            NodeRefType::Connected(ref node_ref) => node_ref.clone(),
            NodeRefType::Disconnected => return,
        };

        self.nodes[node_a].in_ref[conn_a] = NodeRefType::Disconnected;
        self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] = NodeRefType::Disconnected;
        self.invocation_order = None;
    }

    pub fn link(&mut self, node_a: usize, conn_a: usize, node_b: usize, conn_b: usize) {
        self.nodes[node_a].out_ref[conn_a] = NodeRefType::Connected(NodeRef {
            node_idx: node_b,
            conn_idx: conn_b,
        });

        self.nodes[node_b].in_ref[conn_b] = NodeRefType::Connected(NodeRef {
            node_idx: node_a,
            conn_idx: conn_a,
        });
        self.invocation_order = None;
    }

    pub fn unlink_all(&mut self, node: usize) {
        let n = self.nodes[node].clone();

        for out_ref in n.out_ref.iter() {
            match out_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx] =
                        NodeRefType::Disconnected;
                }
                NodeRefType::Disconnected => {}
            }
        }

        for in_ref in n.in_ref.iter() {
            match in_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] =
                        NodeRefType::Disconnected;
                }
                NodeRefType::Disconnected => {}
            }
        }

        self.nodes[node].in_ref = vec![NodeRefType::Disconnected; self.nodes[node].in_ref.len()];
        self.nodes[node].out_ref = vec![NodeRefType::Disconnected; self.nodes[node].out_ref.len()];
        self.invocation_order = None;
    }

    // Reindex a node with a new index, updating all references
    pub fn reindex(&mut self, node: usize, new_idx: usize) {
        self.nodes[node].index = new_idx;
        let n = self.nodes[node].clone();

        for (i, out_ref) in n.out_ref.iter().enumerate() {
            match out_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx] =
                        NodeRefType::Connected(NodeRef {
                            node_idx: new_idx,
                            conn_idx: i,
                        });
                }
                NodeRefType::Disconnected => {}
            }
        }

        for (i, in_ref) in n.in_ref.iter().enumerate() {
            match in_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] =
                        NodeRefType::Connected(NodeRef {
                            node_idx: new_idx,
                            conn_idx: i,
                        });
                }
                NodeRefType::Disconnected => {}
            }
        }
        self.invocation_order = None;
    }

    pub fn apply_offset(&mut self, offset: usize) {
        for node in self.nodes.iter_mut() {
            node.index += offset;

            for out_ref in node.out_ref.iter_mut() {
                match out_ref {
                    NodeRefType::Connected(ref mut node_ref) => {
                        node_ref.node_idx += offset;
                    }
                    NodeRefType::Disconnected => {}
                }
            }

            for in_ref in node.in_ref.iter_mut() {
                match in_ref {
                    NodeRefType::Connected(ref mut node_ref) => {
                        node_ref.node_idx += offset;
                    }
                    NodeRefType::Disconnected => {}
                }
            }
        }
        self.invocation_order = None;
    }

    pub fn visit(&self, node: usize, visited: &mut HashSet<usize>) {
        visited.insert(node);

        for out_ref in self.nodes[node].out_ref.iter() {
            match out_ref {
                NodeRefType::Connected(ref node_ref) => {
                    if !visited.contains(&node_ref.node_idx) {
                        self.visit(node_ref.node_idx, visited);
                    }
                }
                NodeRefType::Disconnected => {}
            }
        }

        for in_ref in self.nodes[node].in_ref.iter() {
            match in_ref {
                NodeRefType::Connected(ref node_ref) => {
                    if !visited.contains(&node_ref.node_idx) {
                        self.visit(node_ref.node_idx, visited);
                    }
                }
                NodeRefType::Disconnected => {}
            }
        }
    }

    pub fn collapse(&mut self, keep: &HashSet<usize>) {
        let mut write_idx = 0;

        for i in 0..self.nodes.len() {
            if keep.contains(&i) {
                if write_idx != i {
                    self.reindex(i, write_idx);
                    self.nodes.swap(write_idx, i);
                }

                write_idx += 1;
            }
        }

        self.nodes.truncate(write_idx);
        self.invocation_order = None;
    }

    pub fn sanitize(&mut self) {
        if self.nodes.len() == 0 {
            return;
        }
        self.shift_levels();
    }

    pub fn shift_levels(&mut self) {
        let min_level = self.nodes.iter().map(|x| x.layer).min().unwrap();

        for node in self.nodes.iter_mut() {
            node.layer -= min_level;
        }
    }

    pub fn trim_to(&mut self, max_nodes: usize) {
        // Nothing to do if we are already small enough
        if self.nodes.len() <= max_nodes {
            return;
        }

        // 1. Decide which nodes we keep: the first `max_nodes` nodes in topological (layer) order.
        let ordered = self.topological_sort();
        let keep_vec: Vec<usize> = ordered.into_iter().take(max_nodes).collect();
        let keep: HashSet<usize> = keep_vec.iter().copied().collect();

        // 2. Disconnect outgoing edges that now point to nodes we will drop.
        for idx in 0..self.nodes.len() {
            if !keep.contains(&idx) {
                continue;
            }

            for out_ref in self.nodes[idx].out_ref.iter_mut() {
                let disconnect = match out_ref {
                    NodeRefType::Connected(node_ref) => !keep.contains(&node_ref.node_idx),
                    NodeRefType::Disconnected => false,
                };

                if disconnect {
                    *out_ref = NodeRefType::Disconnected;
                }
            }
        }

        // 3. Collapse the graph so that only the kept nodes remain and reindex references accordingly.
        self.collapse(&keep);
    }

    pub fn hash_structure(&self) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};

        let mut hasher = DefaultHasher::new();

        // Hash the nodes, but ignore the context
        for node in &self.nodes {
            node.index.hash(&mut hasher);
            node.typ.hash(&mut hasher);
            node.layer.hash(&mut hasher);

            // Hash connections
            for in_ref in &node.in_ref {
                match in_ref {
                    NodeRefType::Connected(r) => {
                        0u8.hash(&mut hasher);
                        r.node_idx.hash(&mut hasher);
                        r.conn_idx.hash(&mut hasher);
                    }
                    NodeRefType::Disconnected => {
                        1u8.hash(&mut hasher);
                    }
                }
            }

            for out_ref in &node.out_ref {
                match out_ref {
                    NodeRefType::Connected(r) => {
                        0u8.hash(&mut hasher);
                        r.node_idx.hash(&mut hasher);
                        r.conn_idx.hash(&mut hasher);
                    }
                    NodeRefType::Disconnected => {
                        1u8.hash(&mut hasher);
                    }
                }
            }
        }

        hasher.finish()
    }
}

pub fn decode_hex(s: &str) -> Result<Vec<u8>, ParseIntError> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16))
        .collect()
}
