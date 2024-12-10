use core::time;
use std::borrow::Borrow;

use libafl::{corpus::{Corpus, InMemoryCorpus}, events::{EventFirer, EventRestarter, SimpleEventManager, SimpleRestartingEventManager}, executors::{Executor, ExitKind, InProcessExecutor}, feedbacks::CrashFeedback, inputs::Input, monitors::SimpleMonitor, schedulers::QueueScheduler, state::{HasCorpus, HasSolutions, State, StdState, Stoppable}, Error, StdFuzzer};
use libafl_bolts::{rands::StdRand, shmem::{ShMemProvider, StdShMemProvider}, tuples::tuple_list};

use crate::{components::graph_input::GraphInput, core::{graph::Graph, harness::Harness, mutation::MutationContext, schema::Schema}};




pub fn minimize(path: String, cycles: usize, gf_harness: Harness, schema: Schema) {
    let mctx = MutationContext::new(schema);
    println!("Computing mutation context...");
    mctx.compute(3);
    println!("Computed mutation context");

    let inp = GraphInput::from_file(path).unwrap();

    let mut objective = CrashFeedback::new();

    let mut harness = |input: &GraphInput| {
        gf_harness.invoke(&input.graph);
        ExitKind::Ok
    };

    let monitor = SimpleMonitor::with_user_monitor(|s| {
        println!("Received signal: {:?}", s);
    });

    let mut shmem_provider = StdShMemProvider::new().unwrap();

    let timeout = time::Duration::from_secs(10);

    let (state, mut mgr) = match SimpleRestartingEventManager::<_,StdState<_,_,_,InMemoryCorpus<GraphInput>>,_>::launch(monitor, &mut shmem_provider)
    {
        // The restarting state will spawn the same process again as child, then restarted it each time it crashes.
        Ok(res) => res,
        Err(err) => match err {
            Error::ShuttingDown => {
                return;
            }
            _ => {
                panic!("Failed to setup the restarter: {err}");
            }
        },
    };

    let (mut state, inp) = match state {
        None => {
            // First execution, use the provided input
            let state = StdState::new(
                StdRand::new(),
                InMemoryCorpus::new(),
                InMemoryCorpus::new(),
                &mut (),
                &mut objective,
            )
            .unwrap();
            (state, inp)
        }
        Some(state) => {
            let sol = state.solutions();
            let min = sol.ids().map(
                |id| {
                    let size = sol.get(id).unwrap().borrow().input().as_ref().unwrap().graph.nodes.len();
                    (id, size)
                }
            ).min_by_key(|x| x.1).unwrap().0;

            let inp = sol.get(min).unwrap().borrow().input().as_ref().unwrap().clone();
            (state, inp)
        }
    };

    let scheduler = QueueScheduler::new();
    let mut fuzzer = StdFuzzer::new(scheduler, (), objective);

    let thresh = inp.graph.nodes.len();
    println!("Current size: {:?}", thresh);
    inp.graph.pprint();

    // Create the executor for an in-process function with one observer for edge coverage and one for the execution time
    let mut executor = InProcessExecutor::with_timeout(
        &mut harness,
        tuple_list!(),
        &mut fuzzer,
        &mut state,
        &mut mgr,
        timeout,
    ).unwrap();

    for _ in 0..cycles {
        let mut g = inp.graph.clone();
        mctx.mutate_random(&mut g);

        if g.nodes.len() < thresh {
            let i2 = GraphInput {
                graph: g,
            };
            let _ = executor.run_target(&mut fuzzer, &mut state, &mut mgr, &i2);
        }
    }

    println!("No new crashes");
    mgr.send_exiting().unwrap();
}
