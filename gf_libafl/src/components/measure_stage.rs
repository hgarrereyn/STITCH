use std::{borrow::Cow, cell::RefCell, rc::Rc};

use libafl::{corpus::{Corpus, Testcase}, executors::Executor, feedbacks::MapIndexesMetadata, schedulers::{MinimizerScheduler, TestcaseScore}, stages::Stage, state::{HasCorpus, HasCurrentTestcase, State, UsesState}, Error, HasMetadata};
use libafl_bolts::{impl_serdeany, Named};
use serde::{Deserialize, Serialize};

use crate::components::graph_input::GraphInput;


#[derive(Debug, Serialize, Deserialize)]
struct GraphTestcaseMetadata {
    pub exec_time: f64,
    pub bailed: bool,
}

impl_serdeany!(GraphTestcaseMetadata);


// A LibAFL stage that periodically writes metadata to a file
pub struct MarkStage<'a, S> {
    invoke_result: Rc<RefCell<&'a mut [u8]>>,
    phantom: std::marker::PhantomData<S>,
}

impl<'a, S> MarkStage<'a, S> {
    pub fn new(invoke_result: Rc<RefCell<&'a mut [u8]>>) -> Self {
        Self {
            phantom: std::marker::PhantomData,
            invoke_result,
        }
    }
}

impl<'a, S> Named for MarkStage<'a, S> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("mark")
    }
}

impl<'a, S> UsesState for MarkStage<'a, S>
where
    S: State,
{
    type State = S;
}

impl<'a, E, EM, Z, S, C> Stage<E, EM, Z> for MarkStage<'a, S>
where
    S: State + HasCorpus<Corpus = C> + HasCurrentTestcase,
    C: Corpus<Input = S::Input>,
    E: UsesState<State = S> + Executor<EM, Z>,
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

        // let input = testcase.input().clone().unwrap();
        drop(testcase);

        // Run the testcase
        executor.run_target(fuzzer, state, manager, &input)?;

        let invoke_result = self.invoke_result.borrow_mut();
        let bailed = invoke_result[0] == 0;

        let mut testcase = state.current_testcase_mut()?;
        testcase.add_metadata(GraphTestcaseMetadata { exec_time: 0.0, bailed });

        println!("Bailed: {}", bailed);

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
            entry.metadata::<GraphTestcaseMetadata>().unwrap().bailed
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
