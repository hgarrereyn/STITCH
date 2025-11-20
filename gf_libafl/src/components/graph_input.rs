
use std::{io::Read};

use ahash::RandomState;
use libafl::inputs::Input;
use libafl_bolts::{ErrorBacktrace, HasLen};
use serde::{Serialize, Deserialize};
use core::hash::{BuildHasher, Hasher};

use gf_core::graph::Graph;

use log;


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphInput {
    pub graph: Graph,
}

impl Input for GraphInput {
    fn generate_name(&self, _id: Option<libafl::corpus::CorpusId>) -> String {
        let json_bytes = serde_json::to_vec(self).unwrap();
        let mut hasher = RandomState::with_seeds(0, 0, 0, 0).build_hasher();
        hasher.write(&json_bytes);
        let hash = format!("{:016x}", hasher.finish());

        format!("graph_{}", hash)
    }
    
    fn to_file<P>(&self, path: P) -> Result<(), libafl::Error>
    where
        P: AsRef<std::path::Path>,
    {
        log::error!("Writing graph to file: {}", path.as_ref().display());
        
        // Ensure invocation order is computed before serialization
        let mut graph_with_order = self.clone();
        graph_with_order.graph.ensure_invocation_order();
        
        libafl_bolts::fs::write_file_atomic(path, &serde_json::to_vec(&graph_with_order).unwrap())
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
