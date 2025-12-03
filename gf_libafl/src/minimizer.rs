use std::{rc::Rc, time};

use libafl::events::SimpleEventManager;
use libafl::executors::InProcessForkExecutor;
use libafl::{corpus::InMemoryCorpus, executors::{Executor, ExitKind}, feedbacks::CrashFeedback, inputs::Input, monitors::SimpleMonitor, schedulers::QueueScheduler, state::StdState, StdFuzzer};
use libafl_bolts::{rands::StdRand, shmem::{ShMemProvider, StdShMemProvider}, tuples::tuple_list};

use crate::components::graph_input::GraphInput;
use gf_core::{harness::{Harness, InvokeResult}, mutation::MutationContext};


pub fn minimize(path: String, output: Option<String>, cycles: usize, minimize_timeout: usize, gf_harness: Harness, full_write: bool, ctx: Rc<MutationContext>) {
    let mut objective = CrashFeedback::new();

    let mut harness = |input: &GraphInput| {
        let res = gf_harness.invoke(&input.graph, None);
        match res {
            InvokeResult::Success => ExitKind::Ok,
            InvokeResult::Bailed(_) => ExitKind::Ok,
            InvokeResult::AssertionViolation(_, _) => ExitKind::Ok,
            InvokeResult::LibraryException(_, _) => ExitKind::Crash,
        }
    };

    let monitor = SimpleMonitor::with_user_monitor(|s| {
        println!("Received signal: {:?}", s);
    });

    let mut mgr = SimpleEventManager::new(monitor);

    let shmem_provider = StdShMemProvider::new().unwrap();

    let timeout = time::Duration::from_secs(10);

    let mut state = StdState::new(
        StdRand::new(),
        InMemoryCorpus::new(),
        InMemoryCorpus::new(),
        &mut (),
        &mut objective,
    ).unwrap();

    let scheduler = QueueScheduler::new();
    let mut fuzzer = StdFuzzer::new(scheduler, (), objective);

    // Create the executor for an in-process function with one observer for edge coverage and one for the execution time
    let mut executor = InProcessForkExecutor::new(
        &mut harness,
        tuple_list!(),
        &mut fuzzer,
        &mut state,
        &mut mgr,
        timeout,
        shmem_provider.clone()
    ).unwrap();

    let mut inp = GraphInput::from_file(path).unwrap();
    let mut thresh = inp.graph.nodes.len();

    let start_time = time::Instant::now();
    for _ in 0..cycles {
        // Exit if the timeout has been reached
        if start_time.elapsed().as_secs() > (minimize_timeout as u64) {
            break;
        }

        let mut g = inp.graph.clone();
        ctx.mutate_random(&mut g);
        let new_size = g.nodes.len();

        if new_size < thresh {
            let i2 = GraphInput {
                graph: g,
                exec_meta: None,
            };
            let res = executor.run_target(&mut fuzzer, &mut state, &mut mgr, &i2);
            match res {
                Ok(ExitKind::Crash) => {
                    inp = i2;
                    thresh = new_size;

                    // Write the new input
                    if full_write {
                        gf_harness.write(&inp.graph);
                    } else {
                        inp.graph.pprint();
                    }
                }
                _ => {}
            }
        }
    }

    gf_harness.write(&inp.graph);

    if let Some(output) = output {
        let inp = GraphInput {
            graph: inp.graph,
            exec_meta: None,
        };
        inp.to_file(output);
    }
}
