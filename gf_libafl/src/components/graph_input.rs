
use std::io::Read;

use libafl::{inputs::Input, HasMetadata};
use libafl_bolts::{ErrorBacktrace, HasLen};
use serde::{Serialize, Deserialize};

use gf_core::graph::Graph;



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
    
    fn to_file<P>(&self, path: P) -> Result<(), libafl::Error>
    where
        P: AsRef<std::path::Path>,
    {
        libafl_bolts::fs::write_file_atomic(path, &serde_json::to_vec(self).unwrap())
    }
    
    fn from_file<P>(path: P) -> Result<Self, libafl::Error>
    where
        P: AsRef<std::path::Path>,
    {
        let mut file = std::fs::File::open(path)?;
        let mut bytes = std::vec![];
        file.read_to_end(&mut bytes)?;
        Ok(serde_json::from_slice(&bytes).map_err(|e| libafl::Error::InvalidCorpus(e.to_string(), ErrorBacktrace::new()))?)
    }
}

impl HasLen for GraphInput {
    fn len(&self) -> usize {
        self.graph.nodes.len()
    }
}
