use std::{borrow::Cow, num::NonZeroUsize, rc::Rc};

use libafl::{corpus::Corpus, mutators::{MutationId, Mutator, MutatorsTuple}, state::{HasCorpus, HasRand, HasSolutions}, HasMetadata};
use libafl_bolts::{rands::Rand, Named};

use stitch_core::mutation::MutationContext;
use log::debug;

use super::graph_input::{GraphExecutionMetadata, GraphInput};
pub struct GraphMutatorSelector<MT> {
    mutators: MT,
}

impl<MT> GraphMutatorSelector<MT> {
    pub fn new(mutators: MT) -> Self {
        Self {
            mutators,
        }
    }
}

impl<MT> Named for GraphMutatorSelector<MT> {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutatorSelector")
    }
}

impl<I, MT, S> Mutator<I, S> for GraphMutatorSelector<MT>
where
    MT: MutatorsTuple<I, S>,
    S: HasMetadata + HasRand + HasSolutions + HasCorpus,
{
    fn mutate(
        &mut self,
        state: &mut S,
        input: &mut I,
    ) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        let index =
            state
                .rand_mut()
                .below(NonZeroUsize::new(self.mutators.len()).unwrap());
        self.mutators.get_and_mutate(MutationId::from(index), state, input)
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
            fn mutate(
                &mut self,
                _state: &mut S,
                input: &mut GraphInput,
            ) -> Result<libafl::mutators::MutationResult, libafl::Error> {
                // Structural mutations invalidate any previous execution metadata.
                input.exec_meta = None;

                let graph = &mut input.graph;
                self.ctx.$func(graph);
                graph.trim_to(self.ctx.opts.max_nodes);
                Ok(libafl::mutators::MutationResult::Mutated)
            }
        }
    };
}

create_graph_mutator!(GraphMutateDeleteOne, mutate_delete_one);
create_graph_mutator!(GraphMutateIsolateComponent, mutate_isolate_component);
create_graph_mutator!(GraphMutateRegenerate, mutate_regenerate);
create_graph_mutator!(GraphMutateAddOne, mutate_add_one);

/// Bail-aware trim/repair mutator operating around the executed frontier.
pub struct GraphMutateFrontierTrimRepair {
    ctx: Rc<MutationContext>,
}

impl GraphMutateFrontierTrimRepair {
    pub fn new(ctx: Rc<MutationContext>) -> Self {
        Self { ctx }
    }
}

impl Named for GraphMutateFrontierTrimRepair {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutateFrontierTrimRepair")
    }
}

impl<S> Mutator<GraphInput, S> for GraphMutateFrontierTrimRepair {
    fn mutate(
        &mut self,
        _state: &mut S,
        input: &mut GraphInput,
    ) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        // Take the execution metadata for this mutation and clear it on the input,
        // so it is never reused after we structurally change the graph.
        let meta = input.exec_meta.take();
        let graph = &mut input.graph;

        if let Some(GraphExecutionMetadata {
            bailed,
            bail_node_idx,
            executed_prefix_len,
            ..
        }) = meta
        {
            // If we never executed anything, fall back to a random mutation.
            if !bailed && executed_prefix_len == 0 {
                self.ctx.mutate_random(graph);
            } else {
                    debug!(
                        "GraphMutateFrontierTrimRepair: bailed={}, bail_node_idx={:?}, executed_prefix_len={}",
                        bailed,
                        bail_node_idx,
                        executed_prefix_len
                    );
                self.ctx.mutate_frontier_trim_and_repair(
                    graph,
                    bail_node_idx,
                    executed_prefix_len,
                );
            }
        } else {
            // No execution metadata yet; just apply a generic structural
            // mutation.
            self.ctx.mutate_random(graph);
        }

        graph.trim_to(self.ctx.opts.max_nodes);
        Ok(libafl::mutators::MutationResult::Mutated)
    }
}

/// Bail-aware extension mutator that grows the graph from the executed
/// frontier.
pub struct GraphMutateFrontierExtend {
    ctx: Rc<MutationContext>,
}

impl GraphMutateFrontierExtend {
    pub fn new(ctx: Rc<MutationContext>) -> Self {
        Self { ctx }
    }
}

impl Named for GraphMutateFrontierExtend {
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutateFrontierExtend")
    }
}

impl<S> Mutator<GraphInput, S> for GraphMutateFrontierExtend {
    fn mutate(
        &mut self,
        _state: &mut S,
        input: &mut GraphInput,
    ) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        // Take the execution metadata and clear it; mutating the graph would
        // otherwise make it stale.
        let meta = input.exec_meta.take();
        let graph = &mut input.graph;

        let executed_prefix_len = meta
            .as_ref()
            .map(|m| m.executed_prefix_len)
            .unwrap_or(0);

        debug!(
            "GraphMutateFrontierExtend: executed_prefix_len={}",
            executed_prefix_len
        );

        self.ctx
            .mutate_frontier_extend(graph, executed_prefix_len);

        graph.trim_to(self.ctx.opts.max_nodes);
        Ok(libafl::mutators::MutationResult::Mutated)
    }
}

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

impl<S, C> Mutator<GraphInput, S> for GraphMutateCrossover
where
    S: HasMetadata + HasRand + HasSolutions + HasCorpus<Corpus = C>,
    C: Corpus<Input = GraphInput>,
{
    fn mutate(
        &mut self,
        state: &mut S,
        input: &mut GraphInput,
    ) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        let corpus_size = state.corpus().count();
        if corpus_size == 0 {
            return Ok(libafl::mutators::MutationResult::Skipped);
        }

        // Crossover changes the graph structure; previous exec metadata no
        // longer describes the resulting graph.
        input.exec_meta = None;

        let other_idx = state
            .rand_mut()
            .below(NonZeroUsize::new(corpus_size).unwrap());
        let other_idx = state.corpus().nth(other_idx);
        let other_input = state.corpus().get(other_idx).unwrap();
        let other_graph = other_input
            .borrow_mut()
            .load_input(state.corpus())
            .unwrap()
            .graph
            .clone();

        self.ctx.mutate_crossover(&mut input.graph, &other_graph);
        Ok(libafl::mutators::MutationResult::Mutated)
    }
}
