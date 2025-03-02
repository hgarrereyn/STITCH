use std::borrow::Cow;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};
use std::path::Path;
use std::fs::File;
use std::io::Write;
use libafl::HasMetadata;
use libafl::{
    stages::Stage,
    state::{UsesState, State},
    Error,
};
use libafl_bolts::{impl_serdeany, Named};
use serde::{Deserialize, Serialize};
use gf_core::{graph::Graph, harness::BailPoint};

#[derive(Default, Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeMetadata {
    // Maps node_type to number of failures
    failure_counts: HashMap<usize, usize>,
    // Maps node_type to a representative failing graph
    failure_examples: HashMap<usize, Graph>,

    success_counts: HashMap<usize, usize>,
    success_examples: HashMap<usize, Graph>,
}

impl_serdeany!(RuntimeMetadata);

// Serializable version of the metadata for writing to file
#[derive(Serialize)]
struct MetadataSnapshot {
    failure_counts: HashMap<usize, usize>,
    total_failures: usize,

    success_counts: HashMap<usize, usize>,
    total_successes: usize,
}

impl RuntimeMetadata {
    pub fn new() -> Self {
        Self {
            failure_counts: HashMap::new(),
            failure_examples: HashMap::new(),

            success_counts: HashMap::new(),
            success_examples: HashMap::new(),
        }
    }

    /// Mark a failure for a specific endpoint and store the failing graph as an example.
    /// This method is thread-safe and performs atomic updates.
    pub fn mark_failure(&mut self, bail_point: BailPoint, failing_graph: &Graph) {
        // Update failure count
        let count = self.failure_counts.entry(bail_point.node_type).or_insert(0);
        *count += 1;
        
        // Store or update the representative failing graph
        self.failure_examples.insert(bail_point.node_type, failing_graph.clone());
    }

    pub fn mark_success(&mut self, success_graph: &Graph) {
        // Update success count
        for node in success_graph.nodes.iter() {
            let count = self.success_counts.entry(node.typ).or_insert(0);
            *count += 1;
        }
        
        // Store or update the representative successful graph
        for node in success_graph.nodes.iter() {
            self.success_examples.insert(node.typ, success_graph.clone());
        }
    }

    /// Get the number of failures for a specific endpoint type
    pub fn get_failure_count(&self, node_type: usize) -> usize {
        *self.failure_counts.get(&node_type).unwrap_or(&0)
    }

    /// Get a reference to the representative failing graph for a specific endpoint type, if one exists
    pub fn get_failure_example(&self, node_type: usize) -> Option<Graph> {
        self.failure_examples.get(&node_type).cloned()
    }

    /// Get the total number of failures across all endpoints
    pub fn total_failures(&self) -> usize {
        self.failure_counts.values().sum()
    }

    pub fn total_successes(&self) -> usize {
        self.success_counts.values().sum()
    }

    /// Get all endpoint types that have had failures
    pub fn failing_endpoints(&self) -> Vec<usize> {
        self.failure_counts.keys().copied().collect()
    }

    /// Write current metadata to a file in JSON format
    fn write_to_file<P: AsRef<Path>>(&self, path: P) -> Result<(), Error> {
        let failure_counts = self.failure_counts.clone();
        let success_counts = self.success_counts.clone();
        
        let snapshot = MetadataSnapshot {
            failure_counts: failure_counts.clone(),
            total_failures: failure_counts.values().sum(),

            success_counts: success_counts.clone(),
            total_successes: success_counts.values().sum(),
        };

        let mut file = File::create(path).map_err(|e| Error::unknown(e.to_string()))?;
        serde_json::to_writer_pretty(&mut file, &snapshot)
            .map_err(|e| Error::unknown(e.to_string()))?;
        file.write_all(b"\n").map_err(|e| Error::unknown(e.to_string()))?;
        Ok(())
    }
}

/// A LibAFL stage that periodically writes metadata to a file
pub struct MetadataLoggerStage<S> {
    local_metadata: Rc<RefCell<RuntimeMetadata>>,
    output_file: String,
    update_interval: Duration,
    last_update: Instant,
    phantom: std::marker::PhantomData<S>,
}

impl<S> MetadataLoggerStage<S> {
    pub fn new(local_metadata: Rc<RefCell<RuntimeMetadata>>, output_file: String, update_interval: Duration) -> Self {
        Self {
            local_metadata,
            output_file,
            update_interval,
            last_update: Instant::now(),
            phantom: std::marker::PhantomData,
        }
    }
}

impl<S> Named for MetadataLoggerStage<S> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("metadata_logger")
    }
}

impl<S> UsesState for MetadataLoggerStage<S>
where
    S: State,
{
    type State = S;
}

impl<E, EM, Z, S> Stage<E, EM, Z> for MetadataLoggerStage<S>
where
    S: State + HasMetadata,
    E: UsesState<State = S>,
    EM: UsesState<State = S>,
    Z: UsesState<State = S>,
{
    fn should_restart(&mut self, _state: &mut Self::State) -> Result<bool, Error> {
        Ok(true)
    }

    fn clear_progress(&mut self, _state: &mut Self::State) -> Result<(), Error> {
        Ok(())
    }

    fn perform(
        &mut self,
        _fuzzer: &mut Z,
        _executor: &mut E,
        state: &mut Self::State,
        _manager: &mut EM,
    ) -> Result<(), Error> {
        let now = Instant::now();
        if now.duration_since(self.last_update) >= self.update_interval {
            let metadata = self.local_metadata.borrow().clone();

            metadata.write_to_file(&self.output_file)?;

            self.last_update = now;
            let failures = metadata.total_failures();
            let successes = metadata.total_successes();
            println!("[RUNTIME] FAILED {} SUCCESS {} :: Unique Successes {}", failures, successes, metadata.success_examples.len());

            state.add_metadata(metadata);
        }
        Ok(())
    }
}
