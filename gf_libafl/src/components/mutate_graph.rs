

use std::{borrow::Cow, num::NonZeroUsize, rc::Rc};

use libafl::{mutators::{MutationResult, Mutator}, state::HasRand};
use libafl_bolts::{rands::Rand, Named};

use gf_core::{pspec::PSpec, sampler::multisampler::{MultiSampler}};

use super::graph_input::GraphInput;


pub struct GraphMutateSolver {
    pspec: PSpec,
    ms: MultiSampler,
    resample_prob: f64,
}

impl GraphMutateSolver {
    pub fn new(pspec: PSpec, ms: MultiSampler, resample_prob: f64) -> Self {
        Self { pspec, ms, resample_prob }
    }
}

impl Named for GraphMutateSolver {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutateSolver")
    }
}

impl<S> Mutator<GraphInput, S> for GraphMutateSolver
where
    S: HasRand
{
    fn mutate(&mut self, state: &mut S, input: &mut GraphInput) -> Result<MutationResult, libafl::Error> {
        // Check if we should resample
        if state.rand_mut().coinflip(self.resample_prob) {
            // Resample
            println!("Resampling");

            let graph = self.ms.sample_one();
            if let Some(graph) = graph {
                input.graph = graph.to_graph(&self.pspec);
                return Ok(MutationResult::Mutated);
            } else {
                return Ok(MutationResult::Skipped);
            }
        }

        Ok(MutationResult::Mutated)
    }
}
