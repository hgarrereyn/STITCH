use std::collections::{HashMap, HashSet};
use std::ffi::{CStr, CString};

use pyo3::ffi::c_str;
use serde::{Serialize, Deserialize};
use colored::Colorize;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

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
            assert!(node.context.len() == schema.endpoints[node.typ].context_size.unwrap(), "Node context size mismatch at {} (has {}, expected {})", idx, node.context.len(), schema.endpoints[node.typ].context_size.unwrap());

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

    pub fn to_testcase_py_object<'py>(&self, py: Python<'py>, schema: &Schema) -> PyResult<PyObject> {
        let graph = PyDict::new(py);

        let order = self.topological_sort();
        let mut id = 0;

        // Map from (endpoint_idx, out_idx) to (type, id)
        let mut storage: HashMap<(usize, usize), (String, usize)> = HashMap::new();

        let py_endpoints = PyList::empty(py);

        for node_idx in order {
            let node = &self.nodes[node_idx];
            let endpoint = &schema.endpoints[node.typ];
            
            let py_endpoint = PyDict::new(py);
            py_endpoint.set_item("name", endpoint.name.clone())?;

            let py_variables = PyList::empty(py);
            for mapping in endpoint.mappings.iter() {
                let (typ, id) = if let Some(in_idx) = mapping.in_idx {
                    // Lookup the input variable
                    let (node_idx, conn_idx) = match &node.in_ref[in_idx] {
                        NodeRefType::Connected(ref node_ref) => (node_ref.node_idx, node_ref.conn_idx),
                        NodeRefType::Disconnected => panic!("Input connection is disconnected at {}", node_idx),
                    };

                    storage.get(&(node_idx, conn_idx)).unwrap()
                } else {
                    // Instantiate a new variable
                    let typ_idx = endpoint.outputs[mapping.out_idx.unwrap()];
                    let typ = schema.types[typ_idx].name.clone();

                    id += 1;
                    &(typ, id)
                };

                // Set the variable info.
                let py_variable = PyDict::new(py);
                py_variable.set_item("type", typ)?;
                py_variable.set_item("id", id)?;
                py_variable.set_item("name", mapping.name.clone())?;
                py_variables.append(py_variable)?;

                // Update storage for output variables
                if let Some(out_idx) = mapping.out_idx {
                    storage.insert((node_idx, out_idx), (typ.clone(), *id));
                }
            }
            py_endpoint.set_item("variables", py_variables)?;

            py_endpoints.append(py_endpoint)?;
        }

        graph.set_item("endpoints", py_endpoints)?;

        let graph = graph.into();
        let testcase = build_testcase(py, &graph)?;
        Ok(testcase)
    }

    pub fn to_py_str_repr<'py>(&self, py: Python<'py>, schema: &Schema) -> Result<String, PyErr> {
        // Convert graph to Python testcase object
        let testcase = self.to_testcase_py_object(py, schema)?;
        
        // Get repr of the testcase object
        let repr = testcase.call_method0(py, "__repr__")?;
        
        // Convert Python string to Rust String
        Ok(repr.extract::<String>(py)?)
    }
}

fn build_testcase(py: Python<'_>, graph: &PyObject) -> PyResult<PyObject> {
    let testcase_code = r#"
from dataclasses import dataclass
from typing import List, Dict

@dataclass
class Variable:
    type: str
    id: int

@dataclass
class Endpoint:
    name: str
    variables: Dict[str, Variable]

@dataclass
class Testcase:
    endpoints: List[Endpoint]

# Convert raw data to Testcase instance
def build_testcase(graph):
    endpoints = []
    for endpoint in graph["endpoints"]:
        variables = {}
        for variable in endpoint["variables"]:
            variables[variable["name"]] = Variable(type=variable["type"], id=variable["id"])
        endpoints.append(Endpoint(name=endpoint["name"], variables=variables))
    return Testcase(endpoints=endpoints)
"#;

    let fun: PyObject = PyModule::from_code(
        py,
        CString::new(testcase_code.as_bytes()).unwrap().as_c_str(),
        c_str!("testcase_module"),
        c_str!("testcase_module")
    )?.getattr("build_testcase")?.into();

    let testcase = fun.call1(py, (graph,))?;
    Ok(testcase)
}
