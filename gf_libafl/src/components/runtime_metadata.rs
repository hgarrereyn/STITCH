use std::borrow::Cow;
use std::cell::RefCell;
use std::rc::Rc;
use std::time::{Duration, Instant};
use std::path::Path;
use std::fs::File;
use std::io::Write;
use gf_core::mutation::MutationContext;
use gf_core::pspec::PSpec;
use gf_core::runtime_metadata::RuntimeMetadataView;
use libafl::HasMetadata;
use libafl::{
    stages::Stage,
    state::{UsesState, State},
    Error,
};
use libafl_bolts::Named;
use serde::{Deserialize, Serialize};


#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EndpointMetadata {
    pub name: String,
    pub success_count: usize,
    pub failure_count: usize,
    pub skipped_count: usize,
    pub executions: usize,
    pub average_millis: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FuzzerRuntimeMetadata {
    pub endpoints: Vec<EndpointMetadata>,
}

impl FuzzerRuntimeMetadata {
    pub fn read_from_view(pspec: &PSpec, view: &mut RuntimeMetadataView) -> Self {
        let mut endpoints = Vec::new();
        for i in 0..pspec.endpoints.len() {
            let stats = view.endpoint_stats(i);
            endpoints.push(EndpointMetadata {
                name: pspec.endpoints[i].name.clone(),
                success_count: stats.success_count,
                failure_count: stats.failure_count,
                skipped_count: stats.skipped_count,
                executions: stats.executions,
                average_millis: stats.average_millis,
            });
        }
        Self { endpoints }
    }

    pub fn write_to_file<P: AsRef<Path>>(&self, path: P) -> Result<(), Error> {
        let mut file = File::create(path).map_err(|e| Error::unknown(e.to_string()))?;
        serde_json::to_writer_pretty(&mut file, &self)
            .map_err(|e| Error::unknown(e.to_string()))?;
        file.write_all(b"\n").map_err(|e| Error::unknown(e.to_string()))?;
        Ok(())
    }
}

// A LibAFL stage that periodically writes metadata to a file
pub struct MetadataLoggerStage<'a, S> {
    metadata: Rc<RefCell<RuntimeMetadataView<'a>>>,
    ctx: Rc<MutationContext>,
    output_file: String,
    update_interval: Duration,
    last_update: Instant,
    phantom: std::marker::PhantomData<S>,
}

impl<'a, S> MetadataLoggerStage<'a, S> {
    pub fn new(metadata: Rc<RefCell<RuntimeMetadataView<'a>>>, ctx: Rc<MutationContext>, output_file: String, update_interval: Duration) -> Self {
        Self {
            metadata,
            ctx,
            output_file,
            update_interval,
            last_update: Instant::now(),
            phantom: std::marker::PhantomData,
        }
    }
}

impl<'a, S> Named for MetadataLoggerStage<'a, S> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("metadata_logger")
    }
}

impl<'a, S> UsesState for MetadataLoggerStage<'a, S>
where
    S: State,
{
    type State = S;
}

impl<'a, E, EM, Z, S> Stage<E, EM, Z> for MetadataLoggerStage<'a, S>
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
        _state: &mut Self::State,
        _manager: &mut EM,
    ) -> Result<(), Error> {
        let now = Instant::now();
        if now.duration_since(self.last_update) >= self.update_interval {
            // Read from shared memory
            let metadata = FuzzerRuntimeMetadata::read_from_view(&self.ctx.pspec, &mut self.metadata.borrow_mut());
            metadata.write_to_file(&self.output_file)?;
            self.last_update = now;
        }
        Ok(())
    }
}
