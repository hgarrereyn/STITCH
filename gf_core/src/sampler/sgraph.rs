use serde::{Serialize, Deserialize};

use crate::{graph::{Graph, Node, NodeRef, NodeRefType}, pspec::{self, PSpec}};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SGraph {
    pub nodes: Vec<SNode>,
    pub edges: Vec<SEdge>,

    pub pspec: Option<PSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SNode {
    pub func_idx: usize,
    pub inputs: Vec<SEdge>,
    pub outputs: Vec<SEdge>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SEdge {
    pub other_idx: usize,
    pub other_conn_idx: usize,

    pub object_idx: usize,
    pub attrs: Vec<SAttr>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SAttr {
    pub name: String,
    pub value: String,
}

impl SGraph {
    pub fn to_dot(&self, pspec: &PSpec) -> String {
        let mut dot = String::new();
        dot.push_str("digraph G {\n");
        dot.push_str("  graph [fontname=\"Roboto Mono\"]\n");
        dot.push_str("  node [fontname=\"Roboto Mono\", shape=rectangle]\n");
        dot.push_str("  edge [fontname=\"Roboto Mono\"]\n");
        dot.push_str("  rankdir=LR\n");
        
        // Define nodes with HTML-like labels to create port structure
        for (node_idx, node) in self.nodes.iter().enumerate() {
            let func = &pspec.endpoints[node.func_idx];
            let func_name = func.name.replace(" ", "_");
            
            // Start building the record label
            let mut label_parts = Vec::new();
            
            // Input ports section (top)
            if !node.inputs.is_empty() {
                let input_ports: Vec<String> = node.inputs.iter().enumerate()
                    .map(|(i, _)| {
                        if i < func.input_vars.len() {
                            format!("<i{}>{}", i, func.input_vars[i])
                        } else {
                            format!("<i{}>in_{}", i, i)
                        }
                    })
                    .collect();
                label_parts.push(format!("{{{}}}", input_ports.join("|")));
            }
            
            // Node name in the middle
            label_parts.push(format!("({}) {}", node_idx, func_name));
            
            // Output ports section (bottom)
            if !node.outputs.is_empty() {
                let output_ports: Vec<String> = node.outputs.iter().enumerate()
                    .map(|(i, _)| {
                        if i < func.output_vars.len() {
                            format!("<o{}>{}", i, func.output_vars[i])
                        } else {
                            format!("<o{}>out_{}", i, i)
                        }
                    })
                    .collect();
                label_parts.push(format!("{{{}}}", output_ports.join("|")));
            }
            
            // Combine all parts with the vertical separator
            let node_label = label_parts.join("|");
            
            // Create the node with its label
            dot.push_str(&format!("  {} [shape=record, label=\"{}\"]\n", node_idx, node_label));
            
            // Create edges with port specifications
            for (out_idx, output) in node.outputs.iter().enumerate() {
                dot.push_str(&format!("  {}:o{} -> {}:i{}\n", 
                    node_idx, 
                    out_idx,
                    output.other_idx,
                    output.other_conn_idx
                ));
            }
        }
        dot.push_str("}\n");
        dot
    }

    pub fn to_graph(&self, pspec: &PSpec) -> Graph {
        let mut nodes = Vec::new();

        for (node_idx, node) in self.nodes.iter().enumerate() {
            let in_ref = node.inputs.iter().map(|e| NodeRefType::Connected(NodeRef {
                node_idx: e.other_idx,
                conn_idx: e.other_conn_idx,
            })).collect();

            let out_ref = node.outputs.iter().map(|e| NodeRefType::Connected(NodeRef {
                node_idx: e.other_idx,
                conn_idx: e.other_conn_idx,
            })).collect();

            let context_size = pspec.endpoints[node.func_idx].context_size.unwrap_or(0);

            nodes.push(Node {
                index: node_idx,
                typ: node.func_idx,
                layer: node_idx as isize,
                in_ref,
                out_ref,
                context: vec![0; context_size],
            });
        }

        Graph { nodes }
    }
}
