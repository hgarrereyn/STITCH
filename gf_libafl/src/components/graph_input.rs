
use std::io::Read;

use ahash::RandomState;
use libafl::inputs::Input;
use libafl_bolts::{ErrorBacktrace, HasLen};
use serde::{Deserialize, Serialize};
use core::hash::{BuildHasher, Hasher};

use gf_core::graph::Graph;

use log;


/// Execution metadata attached directly to each `GraphInput`.
///
/// This is populated by the Analyzer stage when a corpus entry is (re-)executed
/// and is then available to graph and data mutators, and it persists when
/// inputs are serialized to disk.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphExecutionMetadata {
    /// Whether this execution bailed (including assertion violations and
    /// library exceptions).
    pub bailed: bool,
    /// Index of the node where the first bail occurred, if any.
    pub bail_node_idx: Option<usize>,
    /// Endpoint type of the node where the first bail occurred, if any.
    pub bail_node_type: Option<usize>,
    /// Number of nodes that executed in invocation order, including the bail
    /// node if any. For successful executions, this will equal the length of
    /// the invocation order.
    pub executed_prefix_len: usize,
    /// Approximate execution time for this input, in milliseconds.
    pub exec_time_ms: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphInput {
    pub graph: Graph,
    /// Optional execution metadata describing how this graph behaved on its
    /// last run. Newly-generated inputs will have this set to `None` until
    /// analyzed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exec_meta: Option<GraphExecutionMetadata>,
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
