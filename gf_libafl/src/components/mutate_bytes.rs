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

        // Run the inner mutator on the flattened context bytes **without** an
        // additional clone. We move `flat` into the `BytesInput`.
        let mut inp = BytesInput::new(flat);
        let result = self.inner.mutate(state, &mut inp)?;

        if result == libafl::mutators::MutationResult::Skipped {
            return Ok(result);
        }

        // Retrieve the (potentially mutated) bytes.
        let out = inp.borrow().bytes();

        // Update the context bytes with the mutated bytes. If the mutator
        // produced a shorter buffer, we only overwrite the bytes that changed,
        // leaving the remaining bytes intact. If it produced a longer buffer,
        // we truncate to the original length.
        let mut offset = 0;
        let out_len = out.len();

        for (i, size) in sizes.iter().enumerate() {
            if offset >= out_len {
                break; // Nothing more to copy
            }

            let end = offset + size;
            let slice_end = out_len.min(end);
            let slice = &out[offset..slice_end];

            // Copy the mutated slice into the node's context.
            input.graph.nodes[i]
                .context
                .get_mut(..slice.len())
                .expect("slice length within bounds")
                .copy_from_slice(slice);

            offset = end;
        }

        Ok(libafl::mutators::MutationResult::Mutated)

    }
}
