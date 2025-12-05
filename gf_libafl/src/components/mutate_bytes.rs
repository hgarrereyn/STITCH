use std::{borrow::{Borrow, Cow}, num::NonZeroUsize, rc::Rc};

use libafl::{
    corpus::Corpus,
    inputs::{BytesInput, HasMutatorBytes},
    mutators::{MutationResult, Mutator},
    state::{HasCorpus, HasRand},
};
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

/// Select a donor buffer from another corpus entry whose endpoint shares at
/// least one high-level data type with the target endpoint. Returns the raw
/// bytes of the donor buffer, if any.
fn select_donor_hint<S>(
    ctx: &MutationContext,
    state: &mut S,
    target_endpoint_idx: usize,
    max_trials: usize,
) -> Result<Option<Vec<u8>>, libafl::Error>
where
    S: HasRand + HasCorpus,
    S::Corpus: Corpus<Input = GraphInput>,
{
    let target_ep = &ctx.pspec.endpoints[target_endpoint_idx];

    // If the target endpoint has no data type tags, we cannot reason about
    // compatibility, so skip cross-pollination.
    if target_ep.data_types.is_empty() {
        return Ok(None);
    }

    // Nothing to sample from.
    if state.corpus().count_all() == 0 {
        return Ok(None);
    }

    for _ in 0..max_trials {
        let id = libafl::random_corpus_id_with_disabled!(state.corpus(), state.rand_mut());

        // Avoid trivially picking the same testcase we are currently mutating.
        if let Some(cur) = state.corpus().current() {
            if id == *cur {
                continue;
            }
        }

        let mut testcase = state.corpus().get_from_all(id)?.borrow_mut();
        let other = testcase.load_input(state.corpus())?;
        let other_input: &GraphInput = other;

        // Search nodes in the donor graph for compatible endpoints.
        for node in other_input.graph.nodes.iter() {
            let ep_idx = node.typ;
            let donor_ep = &ctx.pspec.endpoints[ep_idx];

            // Require at least one shared data type tag.
            if donor_ep
                .data_types
                .is_empty()
                || !donor_ep
                    .data_types
                    .iter()
                    .any(|dt| target_ep.data_types.iter().any(|t| t == dt))
            {
                continue;
            }

            // Iterate over all hint-backed buffers on this endpoint and try to
            // extract a non-empty buffer.
            for (i, hint_arg) in donor_ep.hint_args.iter().enumerate() {
                let hint_size = donor_ep.hint_args_sizes[i];
                let hint_off = donor_ep.arg_offsets[*hint_arg] as usize;

                // Ensure we have at least a size field.
                if hint_off + 4 > node.context.len() {
                    continue;
                }

                let mut size_bytes = [0u8; 4];
                size_bytes.copy_from_slice(&node.context[hint_off..hint_off + 4]);
                let size = u32::from_le_bytes(size_bytes) as usize;
                if size == 0 {
                    continue;
                }

                let copy_len = core::cmp::min(size, hint_size);
                let end = hint_off + 4 + copy_len;
                if end > node.context.len() {
                    continue;
                }

                let data = node.context[hint_off + 4..end].to_vec();
                if !data.is_empty() {
                    return Ok(Some(data));
                }
            }
        }
    }

    Ok(None)
}

impl<M,S> Mutator<GraphInput,S> for GraphMutateBytes<M,S>
where
    M: Mutator<BytesInput,S>,
    S: HasRand + HasCorpus,
    S::Corpus: Corpus<Input = GraphInput>,
{
    fn mutate(&mut self, state: &mut S, input: &mut GraphInput) -> Result<libafl::mutators::MutationResult, libafl::Error> {
        // 1. Pick a node to mutate, preferring the executed prefix and bail
        //    vicinity when execution metadata is available.
        // 2. Pick one of (flattened fixed-size bytes, a resizable buffer
        //    (hint buffer)).
        // 3. Mutate the chosen bytes with the underlying mutator and
        //    reserialize.

        if input.graph.nodes.is_empty() {
            return Ok(MutationResult::Skipped);
        }

        let node_idx = if let Some(meta) = &input.exec_meta {
            let order = input.graph.get_invocation_order();
            let prefix_len = meta.executed_prefix_len.min(order.len());

            if prefix_len == 0 {
                // No executed prefix recorded yet; fall back to uniform.
                state.rand_mut().below(NonZeroUsize::new(input.graph.nodes.len()).unwrap())
            } else {
                // Focus on the frontier: the last few executed nodes (up to 4).
                let band_width = core::cmp::min(4, prefix_len);
                let start = prefix_len - band_width;
                let band = &order[start..prefix_len];

                // With high probability, mutate within the frontier band; with
                // low probability, pick any node to avoid overfitting.
                if state.rand_mut().coinflip(0.8) {
                    // If we bailed and the bail node is in the band, occasionally
                    // target it directly, otherwise pick a random node in band.
                    if meta.bailed {
                        if let Some(bail_idx) = meta.bail_node_idx {
                            if let Some(&bail_in_band) = band.iter().find(|&&idx| idx == bail_idx) {
                                if state.rand_mut().coinflip(0.5) {
                                    bail_in_band
                                } else {
                                    let local = state.rand_mut().below(NonZeroUsize::new(band.len()).unwrap());
                                    band[local]
                                }
                            } else {
                                let local = state.rand_mut().below(NonZeroUsize::new(band.len()).unwrap());
                                band[local]
                            }
                        } else {
                            let local = state.rand_mut().below(NonZeroUsize::new(band.len()).unwrap());
                            band[local]
                        }
                    } else {
                        let local = state.rand_mut().below(NonZeroUsize::new(band.len()).unwrap());
                        band[local]
                    }
                } else {
                    state.rand_mut().below(NonZeroUsize::new(input.graph.nodes.len()).unwrap())
                }
            }
        } else {
            state.rand_mut().below(NonZeroUsize::new(input.graph.nodes.len()).unwrap())
        };

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

            // With some probability, attempt cross-pollination from another
            // corpus entry that operates on the same logical data type. If no
            // compatible donor is found, fall back to in-place mutation.
            if state.rand_mut().coinflip(self.ctx.opts.cross_pollination_probability) {
                if let Some(donor) = select_donor_hint(&self.ctx, state, pendpoint.idx, 32)? {
                    let new_size = hint_size.min(donor.len());

                    node.context[hint_off..hint_off + 4]
                        .copy_from_slice(&(new_size as u32).to_le_bytes());
                    node.context[hint_off + 4..hint_off + 4 + new_size]
                        .copy_from_slice(&donor[..new_size]);

                    return Ok(libafl::mutators::MutationResult::Mutated);
                }
            }

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
