use std::{borrow::Cow, cell::RefCell, collections::HashSet, rc::Rc, time};

use gf_core::harness::BailInfo;
use libafl::{corpus::{Corpus, Testcase}, executors::Executor, feedbacks::MapIndexesMetadata, inputs::UsesInput, schedulers::{MinimizerScheduler, TestcaseScore}, stages::Stage, state::{HasCorpus, HasCurrentTestcase, State, UsesState}, Error, HasMetadata};
use libafl_bolts::{impl_serdeany, Named};
use serde::{Deserialize, Serialize};

use crate::components::graph_input::GraphInput;


#[derive(Debug, Serialize, Deserialize)]
struct GraphTestcaseMetadata {
    pub exec_time: f64,
    pub bailing: bool
}

impl_serdeany!(GraphTestcaseMetadata);


// A LibAFL stage that periodically writes metadata to a file
pub struct TrimmingStage<'a, S> {
    shared_bail_info: Rc<RefCell<&'a mut [u8]>>,
    phantom: std::marker::PhantomData<S>,
}

impl<'a, S> TrimmingStage<'a, S> {
    pub fn new(shared_bail_info: Rc<RefCell<&'a mut [u8]>>) -> Self {
        Self {
            phantom: std::marker::PhantomData,
            shared_bail_info,
        }
    }
}

impl<'a, S> Named for TrimmingStage<'a, S> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("trimming")
    }
}

impl<'a, S> UsesState for TrimmingStage<'a, S>
where
    S: State,
{
    type State = S;
}

impl<'a, E, EM, Z, S, C> Stage<E, EM, Z> for TrimmingStage<'a, S>
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

        let invoke_result = self.shared_bail_info.borrow().to_vec();
        // For packed structs, we need to be careful about unaligned access
        // Copy the bytes to a properly aligned location first
        let mut aligned_bytes = [0u8; std::mem::size_of::<BailInfo>()];
        aligned_bytes.copy_from_slice(&invoke_result);
        let bail_info = unsafe {
            &*(aligned_bytes.as_ptr() as *const BailInfo)
        };

        if bail_info.bailed {
            let mut graph = input.graph.clone();
            let order = graph.topological_sort();
            let mut keep = HashSet::new();

            let mut hit = false;
            for idx in order.iter() {
                if hit {
                    // Unlink all nodes after the bailed node
                    graph.unlink_all(*idx);
                } else {
                    keep.insert(*idx);
                    if *idx == bail_info.node_idx {
                        hit = true;
                    }
                }
            }

            log::info!("[TRIM] Keeping: {} out of {} ({:.2}%)", keep.len(), graph.nodes.len(), keep.len() as f64 / graph.nodes.len() as f64 * 100.0);
            graph.collapse(&keep);

            let mut testcase = state.current_testcase_mut()?;
            testcase.set_input(GraphInput { graph });
        }

        let mut testcase = state.current_testcase_mut()?;
        testcase.add_metadata(GraphTestcaseMetadata { exec_time, bailing: bail_info.bailed });

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
