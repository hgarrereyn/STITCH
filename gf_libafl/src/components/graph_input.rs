
use libafl::inputs::Input;
use libafl_bolts::HasLen;
use serde::{Serialize, Deserialize};

use crate::core::graph::Graph;



#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphInput {
    pub graph: Graph,
}

impl Input for GraphInput {
    fn generate_name(&self, id: Option<libafl::corpus::CorpusId>) -> String {
        match id {
            Some(id) => format!("graph_{}", id),
            None => "graph_nocorpus".to_string(),
        }
    }
}

impl HasLen for GraphInput {
    fn len(&self) -> usize {
        self.graph.nodes.len()
    }
}
