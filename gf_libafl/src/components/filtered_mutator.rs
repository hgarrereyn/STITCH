use std::{borrow::Cow, cell::RefCell, rc::Rc};

use gf_core::{filter::FilterManager, mutation::MutationContext};
use gf_core::schema::Schema;
use libafl::{mutators::{MutationResult, Mutator}, Error};
use libafl_bolts::Named;

use super::graph_input::GraphInput;




pub struct FilteredMutator<M> {
    manager: Rc<RefCell<FilterManager>>,
    ctx: Rc<MutationContext>,
    max_retries: u32,
    name: Cow<'static, str>,
    inner: M
}

impl<M> FilteredMutator<M> 
where
    M: Named
{
    pub fn new(manager: Rc<RefCell<FilterManager>>, ctx: Rc<MutationContext>, inner: M, max_retries: u32) -> Self {
        Self {
            manager,
            ctx,
            max_retries,
            name: Cow::from(format!("filtered<{}>", inner.name())),
            inner,
        }
    }
}

impl<M> Named for FilteredMutator<M> {
    fn name(&self) -> &Cow<'static, str> {
        &self.name
    }
}

impl<M, S> Mutator<GraphInput, S> for FilteredMutator<M>
where
    M: Mutator<GraphInput, S>
{
    fn mutate(&mut self, state: &mut S, input: &mut GraphInput) -> Result<MutationResult, Error> {
        // We'll attempt up to self.max_retries times to produce a valid mutation.
        let orig_input = input.clone();
        for _ in 0..self.max_retries {
            // First, we let the inner mutator try to mutate the input
            let mut_result = self.inner.mutate(state, input)?;

            // If the inner mutator signaled it did nothing, we skip this iteration
            if mut_result == MutationResult::Skipped {
                *input = orig_input.clone();
                continue;
            }

            // Run the filter function to check if it's valid
            if let Ok(true) = self.manager.borrow().test(&input.graph, &self.ctx.schema) {
                // If it is valid, we've succeeded in producing a valid mutation
                return Ok(MutationResult::Mutated);
            } else {
                // If not valid, revert back to the original input before retrying
                *input = orig_input.clone();
            }
        }

        // If we got here, we couldn't produce a valid mutation in max_retries attempts.
        // Return Skipped so we won't execute the target with this mutation.
        Ok(MutationResult::Skipped)
    }
}
