use std::{borrow::{Borrow, Cow}, num::NonZeroUsize, rc::Rc};

use libafl::{inputs::{BytesInput, HasMutatorBytes}, mutators::{MutationResult, Mutator}, state::HasRand};
use libafl_bolts::{rands::Rand, Named};

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
    S: HasRand
{
    fn mutate(&mut self, state: &mut S, input: &mut GraphInput) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        // 1. Pick a random node
        // 2. Pick one of (flattened fixed-size bytes, a resizable buffer (hint buffer))
        // 3. Mutate the chosen bytes with the underlying mutator and reserialize
        
        let node_idx = state.rand_mut().below(NonZeroUsize::new(input.graph.nodes.len()).unwrap());
        let node = &mut input.graph.nodes[node_idx];
        if node.context.len() == 0 {
            return Ok(MutationResult::Skipped);
        }

        let pendpoint = &self.ctx.pspec.endpoints[node.typ];
        
        let has_hint_buffer = pendpoint.hint_args.len() > 0;
        let has_flat_args = pendpoint.flat_args.len() > 0;
        
        let will_mutate_hints = match (has_hint_buffer, has_flat_args) {
            (true, true) => state.rand_mut().coinflip(0.5),
            (true, false) => true,
            (false, true) => false,
            (false, false) => {
                return Ok(MutationResult::Skipped);
            },
        };

        if will_mutate_hints {
            // Pick a random hint
            let hint_idx = state.rand_mut().below(NonZeroUsize::new(pendpoint.hint_args.len()).unwrap());
            let hint_arg = pendpoint.hint_args[hint_idx];
            let hint_size = pendpoint.hint_args_sizes[hint_idx];
            let hint_off = pendpoint.arg_offsets[hint_arg] as usize;

            // Buffer data starts at hint_off+4
            let mut inp = BytesInput::new(node.context[hint_off+4..hint_off + 4 + hint_size].to_vec());
            let result = self.inner.mutate(state, &mut inp)?;

            if result == libafl::mutators::MutationResult::Skipped {
                return Ok(result);
            }

            let out = inp.borrow().bytes();

            let new_size = hint_size.min(out.len());

            // Copy the mutated hint data back to the node context
            node.context[hint_off+4..hint_off + 4 + new_size].copy_from_slice(&out[..new_size]);
            node.context[hint_off..hint_off + 4].copy_from_slice(&(new_size as u32).to_le_bytes());

            return Ok(libafl::mutators::MutationResult::Mutated);
            
        } else {
            // Pack all non-hint bytes into a single flat buffer and mutate it
            let mut flat = vec![];

            for idx in pendpoint.flat_args.iter() {
                let off = pendpoint.arg_offsets[*idx] as usize;
                let size = pendpoint.arg_sizes[*idx] as usize;

                flat.extend_from_slice(&node.context[off..off + size]);
            }

            let mut inp = BytesInput::new(flat);
            let result = self.inner.mutate(state, &mut inp)?;

            if result == libafl::mutators::MutationResult::Skipped {
                return Ok(result);
            }
            
            let out = inp.borrow().bytes();

            let mut offset = 0;
            let out_len = out.len();
            
            for idx in pendpoint.flat_args.iter() {
                let off = pendpoint.arg_offsets[*idx] as usize;
                let size = pendpoint.arg_sizes[*idx] as usize;

                if offset + size > out_len {
                    // Exit early for smaller buffers
                    break;
                }

                node.context[off..off + size].copy_from_slice(&out[offset..offset + size]);
                offset += size;
            }

            return Ok(libafl::mutators::MutationResult::Mutated);
        }
    }
}
