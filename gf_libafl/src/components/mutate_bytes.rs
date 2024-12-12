use std::{borrow::{Borrow, Cow}, rc::Rc};

use libafl::{inputs::{BytesInput, HasMutatorBytes}, mutators::Mutator};
use libafl_bolts::Named;

use gf_core::mutation::MutationContext;

use super::graph_input::GraphInput;


pub struct GraphMutateBytes<M,S> {
    ctx: Rc<MutationContext>,
    inner: M,
    name: Cow<'static, str>,
    _phantom: std::marker::PhantomData<S>
}

impl<M,S> GraphMutateBytes<M,S>
where
    M: Named
{
    pub fn new(ctx: Rc<MutationContext>, inner: M) -> Self {
        Self {
            ctx,
            name: Cow::from(format!("graph<{}>", inner.name())),
            inner,
            _phantom: std::marker::PhantomData
        }
    }
}

impl<M,S> Named for GraphMutateBytes<M,S>
where
    M: Named
{
    fn name(&self) -> &Cow<'static, str> {
        &self.name
    }
}

impl<M,S> Mutator<GraphInput,S> for GraphMutateBytes<M,S>
where
    M: Mutator<BytesInput,S>,
{
    fn mutate(&mut self, state: &mut S, input: &mut GraphInput) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        let mut flat = vec![];
        let mut sizes = vec![];

        for node in &input.graph.nodes {
            flat.extend_from_slice(node.context.as_slice());
            sizes.push(node.context.len());
        }

        // Run the inner mutator on the flattened context bytes.
        let mut inp = BytesInput::new(flat.clone());
        let result = self.inner.mutate(state, &mut inp)?;

        if result == libafl::mutators::MutationResult::Skipped {
            return Ok(result);
        }

        let out = inp.borrow().bytes();
        if out.len() <= flat.len() {
            flat[..out.len()].copy_from_slice(out);
        } else {
            let sz = flat.len();
            flat.copy_from_slice(&out[..sz]);
        }

        // Update the context bytes with the mutated bytes.
        let mut offset = 0;
        for (i, size) in sizes.iter().enumerate() {
            let start = offset;
            let end = offset + size;
            offset = end;

            input.graph.nodes[i].context.copy_from_slice(&flat[start..end]);
        }

        Ok(libafl::mutators::MutationResult::Mutated)

    }
}
