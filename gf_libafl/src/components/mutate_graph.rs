

use std::{borrow::Cow, rc::Rc};

use libafl::mutators::Mutator;
use libafl_bolts::Named;

use gf_core::mutation::MutationContext;

use super::graph_input::GraphInput;


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
