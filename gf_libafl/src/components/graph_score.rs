use libafl::{
    corpus::{Corpus, Testcase}, inputs::Input, schedulers::testcase_score::TestcaseScore, state::{HasCorpus, State}, Error
};
use libafl_bolts::HasLen;

use crate::components::graph_input::GraphInput;

#[derive(Debug, Clone)]
pub struct GraphTestcaseScore {}

impl<S,C> TestcaseScore<S> for GraphTestcaseScore
where
    S: HasCorpus<Corpus = C>,
    C: Corpus<Input = GraphInput>,
{
    fn compute(
        _state: &S,
        entry: &mut Testcase<<S::Corpus as Corpus>::Input>,
    ) -> Result<f64, Error> {
        let exec_time = entry.exec_time().map_or(1, |d| d.as_millis()) as f64;
        let len = entry.input().as_ref().unwrap().graph.nodes.len();
        Ok(exec_time * len as f64)
    }
}