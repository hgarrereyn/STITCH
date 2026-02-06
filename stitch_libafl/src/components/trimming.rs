use std::{borrow::Cow, cell::RefCell, rc::Rc, time};

use stitch_core::harness::BailInfo;
use libafl::{corpus::{Corpus, Testcase}, executors::Executor, feedbacks::MapIndexesMetadata, inputs::UsesInput, schedulers::{MinimizerScheduler, TestcaseScore}, stages::Stage, state::{HasCorpus, HasCurrentTestcase, State, UsesState}, Error, HasMetadata};
use log::debug;
use libafl_bolts::{impl_serdeany, Named};
use serde::{Deserialize, Serialize};

use crate::components::graph_input::{GraphExecutionMetadata, GraphInput};


#[derive(Debug, Serialize, Deserialize)]
struct GraphTestcaseMetadata {
    pub exec_time_ms: f64,
    pub bailing: bool,
    pub bail_node_idx: Option<usize>,
    pub bail_node_type: Option<usize>,
    pub executed_prefix_len: usize,
}

impl_serdeany!(GraphTestcaseMetadata);


/// Analyzer stage: re-executes new corpus entries once to collect
/// execution/bail metadata and attach it to the testcase and the
/// corresponding `GraphInput`. It no longer mutates or trims graphs.
pub struct AnalyzerStage<'a, S> {
    shared_bail_info: Rc<RefCell<&'a mut [u8]>>,
    phantom: std::marker::PhantomData<S>,
}

impl<'a, S> AnalyzerStage<'a, S> {
    pub fn new(shared_bail_info: Rc<RefCell<&'a mut [u8]>>) -> Self {
        Self {
            phantom: std::marker::PhantomData,
            shared_bail_info,
        }
    }
}

impl<'a, S> Named for AnalyzerStage<'a, S> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("analyzer")
    }
}

impl<'a, S> UsesState for AnalyzerStage<'a, S>
where
    S: State,
{
    type State = S;
}

impl<'a, E, EM, Z, S, C> Stage<E, EM, Z> for AnalyzerStage<'a, S>
where
    S: State + HasCorpus<Corpus = C> + HasCurrentTestcase + UsesInput<Input = GraphInput>,
    C: Corpus<Input = GraphInput>,
    E: UsesState<State = S> + Executor<EM, Z> + UsesInput<Input = S::Input>,
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
        fuzzer: &mut Z,
        executor: &mut E,
        state: &mut Self::State,
        manager: &mut EM,
    ) -> Result<(), Error> {
        // Fetch the current testcase
        let mut testcase = state.current_testcase_mut()?;
        if testcase.has_metadata::<GraphTestcaseMetadata>() {
            return Ok(());
        }

        let input = testcase.load_input(state.corpus())?.clone();

        drop(testcase);

        // Run the testcase
        let start_time = time::Instant::now();
        executor.run_target(fuzzer, state, manager, &input)?;
        let end_time = time::Instant::now();
        let exec_time = end_time.duration_since(start_time).as_millis() as f64;

        let bail_bytes = self.shared_bail_info.borrow();
        // For packed structs, we need to be careful about unaligned access.
        // Copy the bytes to a properly aligned location first, without
        // allocating on the heap.
        let mut aligned_bytes = [0u8; std::mem::size_of::<BailInfo>()];
        aligned_bytes.copy_from_slice(&bail_bytes);
        let bail_info = unsafe { &*(aligned_bytes.as_ptr() as *const BailInfo) };

        // Compute executed prefix in invocation order, including the bail node
        // if any, but do not modify the graph. We keep the borrow for the
        // minimal scope to avoid lifetime issues when we later move `input`.
        let executed_prefix_len = {
            let invocation_order = input.graph.get_invocation_order();
            if bail_info.bailed {
                if let Some(bail_pos) = invocation_order
                    .iter()
                    .position(|&idx| idx == bail_info.node_idx)
                {
                    bail_pos + 1
                } else {
                    // If we can't find the bail node in the current invocation
                    // order (graph changed since last analysis), fall back to
                    // treating the whole graph as executed.
                    invocation_order.len()
                }
            } else {
                invocation_order.len()
            }
        };

        let graph_exec_meta = GraphExecutionMetadata {
            bailed: bail_info.bailed,
            bail_node_idx: if bail_info.bailed {
                Some(bail_info.node_idx)
            } else {
                None
            },
            bail_node_type: if bail_info.bailed {
                Some(bail_info.node_type)
            } else {
                None
            },
            executed_prefix_len,
            exec_time_ms: exec_time,
        };

        debug!(
            "AnalyzerStage: bailed={}, bail_node_idx={:?}, bail_node_type={:?}, executed_prefix_len={}, exec_time_ms={}",
            bail_info.bailed,
            if bail_info.bailed { Some(bail_info.node_idx) } else { None },
            if bail_info.bailed { Some(bail_info.node_type) } else { None },
            executed_prefix_len,
            exec_time,
        );

        // Attach execution metadata to the current testcase input and also
        // store summary metadata for the minimizer scheduler.
        let mut testcase = state.current_testcase_mut()?;
        let mut updated_input = input.clone();
        updated_input.exec_meta = Some(graph_exec_meta);
        testcase.set_input(updated_input);
        testcase.add_metadata(GraphTestcaseMetadata {
            exec_time_ms: exec_time,
            bailing: bail_info.bailed,
            bail_node_idx: if bail_info.bailed {
                Some(bail_info.node_idx)
            } else {
                None
            },
            bail_node_type: if bail_info.bailed {
                Some(bail_info.node_type)
            } else {
                None
            },
            executed_prefix_len,
        });

        Ok(())
    }
}


#[derive(Debug, Clone)]
pub struct BailedLenTestcaseScore {}

impl<S, C> TestcaseScore<S> for BailedLenTestcaseScore
where
    S: HasCorpus<Corpus = C>,
    C: Corpus<Input = GraphInput>,
{
    #[allow(clippy::cast_precision_loss, clippy::cast_lossless)]
    fn compute(
        _state: &S,
        entry: &mut Testcase<<S::Corpus as Corpus>::Input>,
    ) -> Result<f64, Error> {
        let bailed = if entry.has_metadata::<GraphTestcaseMetadata>() {
            entry.metadata::<GraphTestcaseMetadata>().unwrap().bailing
        } else {
            false
        };

        if !bailed {
            Ok(1000.0)
        } else {
            Ok(1.0)
        }
    }
}

pub type IndexesBailedLenMinimizerScheduler<CS, O> =
    MinimizerScheduler<CS, BailedLenTestcaseScore, MapIndexesMetadata, O>;
