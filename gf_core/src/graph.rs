
use std::collections::HashSet;

use serde::{Serialize, Deserialize};
use colored::Colorize;

use super::schema::Schema;

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
    pub nodes: Vec<Node>
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
            let in_ref = node.in_ref.iter().map(|x| match x {
                NodeRefType::Disconnected => format!("{}", "D".red()),
                NodeRefType::Connected(ref y) => format!("{}", format!("{}:{}", y.node_idx, y.conn_idx).blue()),
            }).collect::<Vec<String>>().join(" ");
            let out_ref = node.out_ref.iter().map(|x| match x {
                NodeRefType::Disconnected => format!("{}", "D".red()),
                NodeRefType::Connected(ref y) => format!("{}", format!("{}:{}", y.node_idx, y.conn_idx).blue()),
            }).collect::<Vec<String>>().join(" ");
            let context = format!("{:?}", node.context).yellow();

            println!("    {} t:{} {} in[{}] out[{}] {}", index, typ, layer, in_ref, out_ref, context);
        }

        println!("}}");
    }

    pub fn topological_sort(&self) -> Vec<usize> {
        let mut ordered = (0..self.nodes.len()).collect::<Vec<usize>>();
        ordered.sort_by_key(|&x| self.nodes[x].layer);
        ordered
    }

    pub fn validate(&self, schema: &Schema) {
        for (idx, node) in self.nodes.iter().enumerate() {
            assert!(node.index == idx, "Node index mismatch at {}", idx);
            assert!(node.typ < schema.endpoints.len(), "Node type out of bounds at {}", idx);

            // Ensure node has the correct number of input references
            assert!(node.in_ref.len() == schema.endpoints[node.typ].inputs.len(), "Node input reference count mismatch at {} (has {}, expected {})", idx, node.in_ref.len(), schema.endpoints[node.typ].inputs.len());

            // Ensure node has the correct number of output references
            assert!(node.out_ref.len() == schema.endpoints[node.typ].outputs.len(), "Node output reference count mismatch at {} (has {}, expected {})", idx, node.out_ref.len(), schema.endpoints[node.typ].outputs.len());

            // Ensure the context size matches the schema
            assert!(node.context.len() == schema.endpoints[node.typ].context_size, "Node context size mismatch at {} (has {}, expected {})", idx, node.context.len(), schema.endpoints[node.typ].context_size);

            // Ensure inputs and outputs are connected
            for (i, ref_type) in node.in_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        assert!(false, "Node input connection is disconnected at {} (conn {})", idx, i);
                    },
                    NodeRefType::Connected(ref node_ref) => {
                        assert!(node_ref.node_idx < self.nodes.len(), "Node input connection idx out of bounds at {} (conn {})", idx, i);
                        assert!(node_ref.conn_idx < self.nodes[node_ref.node_idx].out_ref.len(), "Node input connection conn out of bounds at {} (conn {})", idx, i);

                        // Check layer
                        assert!(node.layer > self.nodes[node_ref.node_idx].layer, "Node layer mismatch at {} (conn {})", idx, i);

                        // Ensure the connected node has a corresponding output reference
                        assert!(self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] == NodeRefType::Connected(NodeRef {
                            node_idx: idx,
                            conn_idx: i
                        }), "Node input connection mismatch at {} (conn {})", idx, i);
                    }
                }
            }

            for (i, ref_type) in node.out_ref.iter().enumerate() {
                match ref_type {
                    NodeRefType::Disconnected => {
                        assert!(false, "Node output connection is disconnected at {} (conn {})", idx, i);
                    },
                    NodeRefType::Connected(ref node_ref) => {
                        assert!(node_ref.node_idx < self.nodes.len(), "Node output connection idx out of bounds at {} (conn {})", idx, i);
                        assert!(node_ref.conn_idx < self.nodes[node_ref.node_idx].in_ref.len(), "Node output connection conn out of bounds at {} (conn {})", idx, i);

                        // Check layer
                        assert!(node.layer < self.nodes[node_ref.node_idx].layer, "Node layer mismatch at {} (conn {})", idx, i);

                        // Ensure the connected node has a corresponding input reference
                        assert!(self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx] == NodeRefType::Connected(NodeRef {
                            node_idx: idx,
                            conn_idx: i
                        }), "Node output connection mismatch at {} (conn {})", idx, i);
                    }
                }
            }
        }
    }

    pub fn add_node(&mut self, typ: usize, layer: isize, in_size: usize, out_size: usize, context_size: usize) -> usize {
        let idx = self.nodes.len();
        self.nodes.push(Node {
            index: idx,
            typ,
            layer,
            in_ref: vec![NodeRefType::Disconnected; in_size],
            out_ref: vec![NodeRefType::Disconnected; out_size],
            context: vec![0; context_size],
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
    }

    // Unlink node A from its backward connection
    pub fn unlink_backward(&mut self, node_a: usize, conn_a: usize) {
        let node_ref = match self.nodes[node_a].in_ref[conn_a] {
            NodeRefType::Connected(ref node_ref) => node_ref.clone(),
            NodeRefType::Disconnected => return,
        };

        self.nodes[node_a].in_ref[conn_a] = NodeRefType::Disconnected;
        self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] = NodeRefType::Disconnected;
    }

    pub fn link(&mut self, node_a: usize, conn_a: usize, node_b: usize, conn_b: usize) {
        self.nodes[node_a].out_ref[conn_a] = NodeRefType::Connected(NodeRef {
            node_idx: node_b,
            conn_idx: conn_b
        });

        self.nodes[node_b].in_ref[conn_b] = NodeRefType::Connected(NodeRef {
            node_idx: node_a,
            conn_idx: conn_a
        });
    }

    pub fn unlink_all(&mut self, node: usize) {
        let n = self.nodes[node].clone();

        for out_ref in n.out_ref.iter() {
            match out_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx] = NodeRefType::Disconnected;
                },
                NodeRefType::Disconnected => {},
            }
        }

        for in_ref in n.in_ref.iter() {
            match in_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] = NodeRefType::Disconnected;
                },
                NodeRefType::Disconnected => {},
            }
        }

        self.nodes[node].in_ref = vec![NodeRefType::Disconnected; self.nodes[node].in_ref.len()];
        self.nodes[node].out_ref = vec![NodeRefType::Disconnected; self.nodes[node].out_ref.len()];
    }

    // Reindex a node with a new index, updating all references
    pub fn reindex(&mut self, node: usize, new_idx: usize) {
        self.nodes[node].index = new_idx;
        let n = self.nodes[node].clone();

        for (i, out_ref) in n.out_ref.iter().enumerate() {
            match out_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].in_ref[node_ref.conn_idx] = NodeRefType::Connected(NodeRef {
                        node_idx: new_idx,
                        conn_idx: i
                    });
                },
                NodeRefType::Disconnected => {},
            }
        }

        for (i, in_ref) in n.in_ref.iter().enumerate() {
            match in_ref {
                NodeRefType::Connected(ref node_ref) => {
                    self.nodes[node_ref.node_idx].out_ref[node_ref.conn_idx] = NodeRefType::Connected(NodeRef {
                        node_idx: new_idx,
                        conn_idx: i
                    });
                },
                NodeRefType::Disconnected => {},
            }
        }
    }

    pub fn visit(&self, node: usize, visited: &mut HashSet<usize>) {
        visited.insert(node);

        for out_ref in self.nodes[node].out_ref.iter() {
            match out_ref {
                NodeRefType::Connected(ref node_ref) => {
                    if !visited.contains(&node_ref.node_idx) {
                        self.visit(node_ref.node_idx, visited);
                    }
                },
                NodeRefType::Disconnected => {},
            }
        }

        for in_ref in self.nodes[node].in_ref.iter() {
            match in_ref {
                NodeRefType::Connected(ref node_ref) => {
                    if !visited.contains(&node_ref.node_idx) {
                        self.visit(node_ref.node_idx, visited);
                    }
                },
                NodeRefType::Disconnected => {},
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
    }

    pub fn sanitize(&mut self) {
        self.shift_levels();
    }

    pub fn shift_levels(&mut self) {
        let min_level = self.nodes.iter().map(|x| x.layer).min().unwrap();

        for node in self.nodes.iter_mut() {
            node.layer -= min_level;
        }
    }
}
