use std::fs;
use std::io::Write;
use std::{cell::RefCell, fs::OpenOptions, path::PathBuf, process, rc::Rc, time::Duration};

use gf_core::{
    harness::{BailInfo, Harness, InvokeResult},
    mutation::MutationContext,
    runtime_metadata::RuntimeMetadata,
};
use libafl::schedulers::powersched::PowerSchedule;
use libafl::schedulers::StdWeightedScheduler;
use libafl::Evaluator;
use libafl::{
    corpus::{Corpus, InMemoryOnDiskCorpus, OnDiskCorpus},
    events::SimpleEventManager,
    executors::{ExitKind, InProcessForkExecutor},
    feedback_or,
    feedbacks::{CrashFeedback, MaxMapFeedback, TimeFeedback},
    monitors::SimpleMonitor,
    mutators::{havoc_mutations_no_crossover, tokens_mutations, StdMOptMutator, Tokens},
    observers::{CanTrack, HitcountsMapObserver, TimeObserver},
    stages::StdMutationalStage,
    state::{HasCorpus, StdState},
    Error, Fuzzer, HasMetadata, StdFuzzer,
};
use libafl_bolts::shmem::{ShMemProvider, StdShMemProvider};
use libafl_bolts::{current_time, AsSliceMut};
use libafl_bolts::{
    rands::StdRand,
    tuples::{tuple_list, Merge},
};
use libafl_targets::{std_edges_map_observer, EDGES_MAP_ALLOCATED_SIZE, EDGES_MAP_PTR};
use log::LevelFilter;
use simple_logger::SimpleLogger;

use crate::components::runtime_metadata::MetadataLoggerStage;
use crate::components::trimming::{AnalyzerStage, IndexesBailedLenMinimizerScheduler};
use crate::components::{
    graph_input::GraphInput,
    mutate_bytes::GraphMutateBytes,
    mutate_graph::{
        GraphMutateCrossover, GraphMutateFrontierExtend, GraphMutateFrontierTrimRepair,
        GraphMutateRegenerate, GraphMutatorSelector,
    },
};

/// The actual fuzzer
pub fn fuzz(
    gf_harness: Harness,
    ctx: Rc<MutationContext>,
    out_dir: &PathBuf,
    seed_dir: &PathBuf,
    raw_in_dir: Option<PathBuf>,
    tokenfile: Option<PathBuf>,
    logfile: &PathBuf,
    timeout: Duration,
    _rss_limit_mb: Option<u64>,
    keep_stdout: bool,
    verbose: bool,
) -> Result<(), Error> {
    if verbose {
        SimpleLogger::new()
            .with_level(LevelFilter::Info)
            .init()
            .unwrap();
    }

    // Read raw inputs into the ctx
    if let Some(raw_in_dir) = raw_in_dir {
        for entry in fs::read_dir(raw_in_dir)? {
            let entry = entry?;
            let path = entry.path();
            let contents = fs::read_to_string(path)?;
            ctx.raw_inputs.borrow_mut().push(contents);
        }
    }
    println!("Read {} raw inputs", ctx.raw_inputs.borrow().len());

    let corpus_dir = out_dir.join("queue");
    let objective_dir = out_dir.join("crashes");

    let log = RefCell::new(OpenOptions::new().append(true).create(true).open(logfile)?);

    #[cfg(unix)]
    let mut stdout_cpy = {
        // We forward all outputs to dev/null, but keep a copy around for the fuzzer output.
        //
        // # Safety
        // stdout and stderr should still be open at this point in time.
        let (new_stdout, new_stderr) = if keep_stdout {
            use std::{io, os::fd::AsRawFd};

            (io::stdout().as_raw_fd(), io::stderr().as_raw_fd())
        } else {
            unsafe { libafl_bolts::dup_and_mute_outputs()? }
        };

        // If we are debugging, re-enable target stderror.
        if std::env::var("LIBAFL_FUZZBENCH_DEBUG").is_ok() {
            // Nobody else uses the new stderror here.
            use std::{io, os::fd::AsRawFd};
            libafl_bolts::os::dup2(new_stderr, io::stderr().as_raw_fd())?;
        }

        #[cfg(unix)]
        {
            use std::{fs::File, os::fd::FromRawFd};
            // The new stdout is open at this point, and we will not use it anywhere else.
            unsafe { File::from_raw_fd(new_stdout) }
        }
    };

    // 'While the monitor are state, they are usually used in the broker - which is likely never restarted
    let monitor = SimpleMonitor::with_user_monitor(|s| {
        #[cfg(unix)]
        writeln!(&mut stdout_cpy, "{s}").unwrap();

        writeln!(log.borrow_mut(), "{:?} {s}", current_time()).unwrap();
        log.borrow_mut().flush().unwrap();
    });

    // We need a shared map to store our state before a crash.
    // This way, we are able to continue fuzzing afterwards.
    let mut shmem_provider = StdShMemProvider::new()?;

    println!(
        "Allocating shared memory for edges map: {}",
        EDGES_MAP_ALLOCATED_SIZE
    );
    let mut shmem = shmem_provider.new_shmem(EDGES_MAP_ALLOCATED_SIZE).unwrap();
    let shmem_buf = shmem.as_slice_mut();
    unsafe {
        EDGES_MAP_PTR = shmem_buf.as_mut_ptr();
    }

    // Setup shared memory for bail info (exactly the size needed)
    // BailInfo is a fixed-size structure: bool + 2 usize = 1 + 8 + 8 = 17 bytes
    let bail_info_size = std::mem::size_of::<BailInfo>();
    let mut bail_info_shmem = shmem_provider.new_shmem(bail_info_size).unwrap();
    let bail_info_shmem_buf = bail_info_shmem.as_slice_mut();

    let shared_bail_info = Rc::new(RefCell::new(bail_info_shmem_buf));

    // Setup shared memory for runtime metadata (per-endpoint stats).
    // This buffer is read/written by the harness on each execution, and periodically
    // snapshotted to disk by a LibAFL stage outside the hot loop.
    let mut metadata_shmem = shmem_provider
        .new_shmem(RuntimeMetadata::compute_size(ctx.pspec.endpoints.len()))
        .unwrap();
    let metadata_shmem_buf = metadata_shmem.as_slice_mut();
    // Start from a clean slate for all counters and timing data.
    metadata_shmem_buf.fill(0);
    let runtime_metadata = Rc::new(RefCell::new(gf_core::runtime_metadata::RuntimeMetadataView::new(
        metadata_shmem_buf,
    )));

    let obs = unsafe { std_edges_map_observer("edges") };

    let mut mgr = SimpleEventManager::new(monitor);

    // Create an observation channel using the coverage map
    // We don't use the hitcounts (see the Cargo.toml, we use pcguard_edges)
    let edges_observer = HitcountsMapObserver::new(obs).track_indices();

    // Create an observation channel to keep track of the execution time
    let time_observer = TimeObserver::new("time");

    let map_feedback = MaxMapFeedback::new(&edges_observer);

    // Feedback to rate the interestingness of an input
    // This one is composed by two Feedbacks in OR
    let mut feedback = feedback_or!(
        // New maximization map feedback linked to the edges observer and the feedback state
        map_feedback,
        // Time feedback, this one does not need a feedback state
        TimeFeedback::new(&time_observer)
    );

    // A feedback to choose if an input is a solution or not
    let mut objective = CrashFeedback::new();

    // If not restarting, create a State from scratch
    // let state = None;
    let mut state = StdState::new(
        // RNGe
        StdRand::new(),
        // Corpus that will be evolved, we keep it in memory for performance
        InMemoryOnDiskCorpus::new(corpus_dir).unwrap(),
        // Corpus in which we store solutions (crashes in this example),
        // on disk so the user can get them after stopping the fuzzer
        OnDiskCorpus::new(objective_dir).unwrap(),
        // States of the feedbacks.
        // The feedbacks can report the data that should persist in the State.
        &mut feedback,
        // Same for objective feedbacks
        &mut objective,
    )
    .unwrap();

    println!("Let's fuzz :)");

    // Setup a MOPT mutator
    let mutator = StdMOptMutator::new(
        &mut state,
        havoc_mutations_no_crossover().merge(tokens_mutations()),
        7,
        5,
    )?;

    let graph_mutator = StdMutationalStage::new(GraphMutatorSelector::new(
        0.01,
        tuple_list!(
            GraphMutateFrontierTrimRepair::new(ctx.clone()),
            GraphMutateFrontierExtend::new(ctx.clone()),
            GraphMutateRegenerate::new(ctx.clone()),
            GraphMutateCrossover::new(ctx.clone()),
        ),
    ));

    let context_stage = StdMutationalStage::new(GraphMutateBytes::new(ctx.clone(), mutator));

    // let scheduler = QueueScheduler::new();
    let scheduler = IndexesBailedLenMinimizerScheduler::new(
        &edges_observer,
        StdWeightedScheduler::with_schedule(
            &mut state,
            &edges_observer,
            Some(PowerSchedule::fast()),
        ),
    );

    // A fuzzer with feedbacks and a corpus scheduler
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);

    // The wrapped harness function, calling out to the LLVM-style harness
    let runtime_metadata_for_harness = runtime_metadata.clone();

    let mut harness = |input: &GraphInput| {
        // Invoke the harness in the sandboxed environment
        let mut local_view = runtime_metadata_for_harness.borrow_mut();
        let res = gf_harness.invoke(&input.graph, Some(&mut *local_view));

        // Classify this execution in terms of endpoint outcomes, using the
        // invocation order. This lets us distinguish successes, failures, and
        // endpoints that were *skipped* because an earlier endpoint failed.
        let bail_node_idx = match &res {
            InvokeResult::Success => None,
            InvokeResult::Bailed(bp) => Some(bp.node_idx),
            InvokeResult::AssertionViolation(bp, _) => Some(bp.node_idx),
            InvokeResult::LibraryException(bp, _) => Some(bp.node_idx),
        };

        let invocation_order = input.graph.get_invocation_order();
        if let Some(bail_idx) = bail_node_idx {
            // Find where the bail happened in the invocation order
            if let Some(bail_pos) = invocation_order.iter().position(|&idx| idx == bail_idx) {
                // Endpoints after bail_pos were skipped; update shared runtime stats.
                for i in (bail_pos + 1)..invocation_order.len() {
                    let node_idx = invocation_order[i];
                    let ep_idx = input.graph.nodes[node_idx].typ;
                    // Mark this endpoint as skipped in the runtime metadata.
                    local_view.endpoint_stats_mut(ep_idx).mark_skipped();
                }
            }
        }

        // Store the bail info in the shared memory
        let bail_info = res.to_bail_info();
        let bail_info_bytes = unsafe {
            std::slice::from_raw_parts(
                &bail_info as *const BailInfo as *const u8,
                std::mem::size_of::<BailInfo>(),
            )
        };
        (*shared_bail_info).borrow_mut().copy_from_slice(bail_info_bytes);

        match res {
            InvokeResult::Success => ExitKind::Ok,
            InvokeResult::Bailed(_) => ExitKind::Ok,
            InvokeResult::AssertionViolation(_, msg) => {
                // Abort with the message. This will be caught by LibAFL's crash handling.
                panic!("{}", msg);
            }
            InvokeResult::LibraryException(_, _) => ExitKind::Ok,
        }
    };

    // Create the executor for an in-process function with one observer for edge coverage and one for the execution time
    let mut executor = InProcessForkExecutor::new(
        &mut harness,
        tuple_list!(edges_observer, time_observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
        timeout,
        shmem_provider.clone(),
    )?;

    let analyzer_stage = AnalyzerStage::new(shared_bail_info.clone());

    // Periodically dump aggregated runtime metadata to `runtime_stats.json` in the
    // output directory. This runs outside the hot execution loop and avoids
    // per-execution file I/O.
    let metadata_logger = MetadataLoggerStage::new(
        runtime_metadata,
        ctx.clone(),
        out_dir
            .join("runtime_stats.json")
            .to_string_lossy()
            .to_string(),
        Duration::from_secs(2),
    );

    let mut stages = tuple_list!(metadata_logger, analyzer_stage, graph_mutator, context_stage,);

    // Read tokens
    if state.metadata_map().get::<Tokens>().is_none() {
        let mut toks = Tokens::default();
        if let Some(tokenfile) = tokenfile {
            toks.add_from_file(tokenfile)?;
        }
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        {
            toks += libafl_targets::autotokens()?;
        }

        if !toks.is_empty() {
            state.add_metadata(toks);
        }
    }

    // In case the corpus is empty (on first run), reset
    if state.must_load_initial_inputs() {
        state
            .load_initial_inputs(&mut fuzzer, &mut executor, &mut mgr, &[seed_dir.clone()])
            .unwrap_or_else(|_| {
                println!("Failed to load initial corpus at {:?}", &seed_dir);
                process::exit(0);
            });
        println!("We imported {} inputs from disk.", state.corpus().count());
    }

    // Generate initial seeds
    let mut initial_seeds = vec![];
    for idx in ctx.cache.borrow().enabled_endpoints.iter() {
        initial_seeds.push(ctx.generate_seeded(*idx));
    }
    println!("Initial seeds: {:?}", initial_seeds.len());

    // Add to fuzzer
    for seed in initial_seeds {
        let seed_input = GraphInput {
            graph: seed.clone(),
            exec_meta: None,
        };
        fuzzer.add_input(&mut state, &mut executor, &mut mgr, seed_input)?;
    }

    // reopen file to make sure we're at the end
    log.replace(OpenOptions::new().append(true).create(true).open(logfile)?);

    log::info!("Starting fuzzing loop");
    fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)?;

    Ok(())
}
