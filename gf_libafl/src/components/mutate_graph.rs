use std::{borrow::Cow, num::NonZeroUsize, rc::Rc};

use libafl::{corpus::{Corpus, CorpusId}, inputs::UsesInput, mutators::{MutationId, Mutator, MutatorsTuple}, state::{HasCorpus, HasRand, HasSolutions}, HasMetadata};
use libafl_bolts::{rands::Rand, Named};

use gf_core::mutation::MutationContext;

use super::graph_input::GraphInput;

pub struct GraphMutatorSelector<MT> {
    graph_select_prob: f64,
    max_stack: usize,
    mutators: MT
}

impl<MT> GraphMutatorSelector<MT> {
    pub fn new(graph_select_prob: f64, max_stack: usize, mutators: MT) -> Self {
        Self { graph_select_prob, max_stack, mutators }
    }
}

impl<MT> Named for GraphMutatorSelector<MT> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutatorSelector")
    }
}

impl<I,MT,S> Mutator<I,S> for GraphMutatorSelector<MT> 
where
    MT: MutatorsTuple<I,S>,
    S: HasMetadata + HasRand + HasSolutions + HasCorpus,
{
    fn mutate(&mut self, state: &mut S, input: &mut I) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        if state.rand_mut().coinflip(self.graph_select_prob) {
            for _ in 0..(state.rand_mut().below(NonZeroUsize::new(self.max_stack).unwrap()) + 1) {
                let index = state.rand_mut().below(NonZeroUsize::new(self.mutators.len()).unwrap());
                self.mutators.get_and_mutate(MutationId::from(index), state, input)?;
            }
            Ok(libafl::mutators::MutationResult::Mutated)
        } else {
            Ok(libafl::mutators::MutationResult::Skipped)
        }
    }
}


macro_rules! create_graph_mutator {
    ($name:ident, $func:ident) => {
        pub struct $name {
            ctx: Rc<MutationContext>,
        }

        impl $name {
            pub fn new(ctx: Rc<MutationContext>) -> Self {
                Self { ctx }
            }
        }

        impl Named for $name {
            fn name(&self) -> &Cow<'static, str> {
                &Cow::Borrowed(stringify!($name))
            }
        }

        impl<S> Mutator<GraphInput, S> for $name {
            fn mutate(&mut self, _state: &mut S, input: &mut GraphInput) -> Result<libafl::mutators::MutationResult, libafl::Error> {
                let graph = &mut input.graph;
                self.ctx.$func(graph);
                Ok(libafl::mutators::MutationResult::Mutated)
            }
        }
    };
}

create_graph_mutator!(GraphMutateDeleteOne, mutate_delete_one);
create_graph_mutator!(GraphMutateIsolateComponent, mutate_isolate_component);
create_graph_mutator!(GraphMutateRegenerate, mutate_regenerate);
create_graph_mutator!(GraphMutateAddOne, mutate_add_one);

pub struct GraphMutateCrossover {
    ctx: Rc<MutationContext>,
}

impl GraphMutateCrossover {
    pub fn new(ctx: Rc<MutationContext>) -> Self {
        Self { ctx }
    }
}

impl Named for GraphMutateCrossover {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutateCrossover")
    }
}

impl<S,C> Mutator<GraphInput, S> for GraphMutateCrossover
where
    S: HasMetadata + HasRand + HasSolutions + HasCorpus<Corpus = C>,
    C: Corpus<Input = GraphInput>,
{
    fn mutate(&mut self, state: &mut S, input: &mut GraphInput) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        let corpus_size = state.corpus().count();
        if corpus_size == 0 {
            return Ok(libafl::mutators::MutationResult::Skipped);
        }

        let other_idx = state.rand_mut().below(NonZeroUsize::new(corpus_size).unwrap());
        let other_input = state.corpus().get(CorpusId::from(other_idx)).unwrap();
        let other_graph = other_input.borrow_mut().load_input(state.corpus()).unwrap().graph.clone();
        
        self.ctx.mutate_crossover(&mut input.graph, &other_graph);
        Ok(libafl::mutators::MutationResult::Mutated)
    }
}
