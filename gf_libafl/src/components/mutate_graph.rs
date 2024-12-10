

use std::{borrow::{Borrow, Cow}, rc::Rc};

use libafl::{inputs::{BytesInput, HasMutatorBytes}, mutators::Mutator};
use libafl_bolts::Named;

use crate::core::mutation::MutationContext;

use super::graph_input::GraphInput;


pub struct GraphMutateDeleteOne {
    ctx: Rc<MutationContext>,
}

impl GraphMutateDeleteOne
{
    pub fn new(ctx: Rc<MutationContext>) -> Self {
        Self {
            ctx,
        }
    }
}

impl Named for GraphMutateDeleteOne
{
    fn name(&self) -> &Cow<'static, str> {
        &Cow::Borrowed("GraphMutateDeleteOne")
    }
}

impl<S> Mutator<GraphInput,S> for GraphMutateDeleteOne
{
    fn mutate(&mut self, _state: &mut S, input: &mut GraphInput) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        let graph = &mut input.graph;
        self.ctx.mutate_delete_one(graph);
        Ok(libafl::mutators::MutationResult::Mutated)
    }
}
