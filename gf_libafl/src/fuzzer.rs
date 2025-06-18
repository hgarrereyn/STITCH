use core::{cell::RefCell, time::Duration};
#[cfg(unix)]
use std::os::unix::io::{AsRawFd, FromRawFd};
use std::{
    borrow::BorrowMut, fs::{File, OpenOptions}, io::{self, Write}, num::NonZeroUsize, path::PathBuf, process, rc::Rc, sync::Arc
};
use tempfile;

use libafl::{
    corpus::{Corpus, InMemoryOnDiskCorpus, OnDiskCorpus}, events::{SimpleEventManager, SimpleRestartingEventManager}, executors::{inprocess::InProcessExecutor, ExitKind, ForkserverExecutor, InProcessForkExecutor}, feedback_or, feedbacks::{CrashFeedback, MaxMapFeedback, TimeFeedback}, fuzzer::{Fuzzer, StdFuzzer}, monitors::SimpleMonitor, mutators::{
        havoc_mutations_no_crossover, token_mutations::I2SRandReplace, tokens_mutations, StdMOptMutator, StdScheduledMutator, Tokens
    }, observers::{CanTrack, HitcountsMapObserver, StdMapObserver, TimeObserver}, schedulers::{powersched::PowerSchedule, IndexesLenTimeMinimizerScheduler, QueueScheduler, StdWeightedScheduler}, stages::{
        StdMutationalStage,
        TracingStage,
    }, state::{HasCorpus, StdState}, Error, Evaluator, HasMetadata
};
use libafl_bolts::{
    current_time, rands::StdRand, shmem::{ShMemProvider, StdShMemProvider}, tuples::{tuple_list, Merge}, AsSlice, AsSliceMut
};
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use libafl_targets::autotokens;
use libafl_targets::{
    libfuzzer_initialize, std_edges_map_observer, CmpLogObserver, EDGES_MAP_PTR, EDGES_MAP_DEFAULT_SIZE
};
#[cfg(unix)]
use nix::unistd::dup;

use crate::components::{graph_input::GraphInput, mutate_bytes::GraphMutateBytes, mutate_graph::{GraphMutateAddOne, GraphMutateCrossover, GraphMutateDeleteOne, GraphMutateIsolateComponent, GraphMutateRegenerate, GraphMutatorSelector}, mutate_graph_sat::GraphMutateSolver, runtime_metadata::MetadataLoggerStage};
use gf_core::{harness::{Harness, InvokeResult}, mutation::MutationContext, pspec::PSpec, runtime_metadata::{RuntimeMetadata, RuntimeMetadataView}, sampler::{encoder::EncoderConfig, multisampler::{MultiSampler, SamplerConstraint}}};

/// The actual fuzzer
pub fn fuzz(
    gf_harness: Harness,
    ctx: Rc<MutationContext>,
    out_dir: &PathBuf,
    seed_dir: &PathBuf,
    tokenfile: Option<PathBuf>,
    logfile: &PathBuf,
    timeout: Duration
) -> Result<(), Error>
{
    let corpus_dir = out_dir.join("queue");
    let objective_dir = out_dir.join("crashes");
    let runtime_metadata_path = out_dir.join("runtime_metadata.json");

    let log = RefCell::new(OpenOptions::new().append(true).create(true).open(logfile)?);

    #[cfg(unix)]
    let mut stdout_cpy = unsafe {
        let new_fd = dup(io::stdout().as_raw_fd())?;
        File::from_raw_fd(new_fd)
    };
    #[cfg(unix)]
    let file_null = File::open("/dev/null")?;

    // 'While the monitor are state, they are usually used in the broker - which is likely never restarted
    let monitor = SimpleMonitor::with_user_monitor(|s| {
        #[cfg(unix)]
        writeln!(&mut stdout_cpy, "{s}").unwrap();
        #[cfg(windows)]
        println!("{s}");
        writeln!(log.borrow_mut(), "{:?} {s}", current_time()).unwrap();
    });

    // We need a shared map to store our state before a crash.
    // This way, we are able to continue fuzzing afterwards.
    let mut shmem_provider = StdShMemProvider::new()?;

    let mut shmem;
    unsafe{
        shmem = shmem_provider.new_shmem(EDGES_MAP_DEFAULT_SIZE).unwrap();
    }
    let shmem_buf = shmem.as_slice_mut();
    unsafe{
        EDGES_MAP_PTR = shmem_buf.as_mut_ptr();
    }

    // Setup shared memory for runtime metadata
    let mut metadata_shmem = shmem_provider.new_shmem(
        RuntimeMetadata::compute_size(ctx.pspec.endpoints.len())
    ).unwrap();
    let mut metadata_shmem_buf = metadata_shmem.as_slice_mut();
    let RUNTIME_METADATA_PTR = unsafe {
        metadata_shmem_buf.as_slice_mut()
    };
    
    let obs = unsafe { std_edges_map_observer("edges") };

    let (state, mut mgr) = match SimpleRestartingEventManager::launch(monitor, &mut shmem_provider)
    {
        // The restarting state will spawn the same process again as child, then restarted it each time it crashes.
        Ok(res) => res,
        Err(err) => match err {
            Error::ShuttingDown => {
                return Ok(());
            }
            _ => {
                panic!("Failed to setup the restarter: {err}");
            }
        },
    };

    // let mut state = None;
    // let mut mgr = SimpleEventManager::new(monitor);

    // Create an observation channel using the coverage map
    // We don't use the hitcounts (see the Cargo.toml, we use pcguard_edges)
    let edges_observer =
        HitcountsMapObserver::new(obs).track_indices();

    // Create an observation channel to keep track of the execution time
    let time_observer = TimeObserver::new("time");

    let cmplog_observer = CmpLogObserver::new("cmplog", true);

    let map_feedback = MaxMapFeedback::new(&edges_observer);

    // let calibration = CalibrationStage::new(&map_feedback);

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
    let mut state = state.unwrap_or_else(|| {
        StdState::new(
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
        .unwrap()
    });

    println!("Let's fuzz :)");

    // Setup a randomic Input2State stage
    let i2s = StdMutationalStage::with_max_iterations(
        GraphMutateBytes::new(ctx.clone(), StdScheduledMutator::new(tuple_list!(I2SRandReplace::new()))),
        NonZeroUsize::new(1).unwrap()
    );

    // Setup a MOPT mutator
    let mutator = StdMOptMutator::new(
        &mut state,
        havoc_mutations_no_crossover().merge(tokens_mutations()),
        7,
        5,
    )?;

    // let graph_mutator = StdMutationalStage::new(
    //     FilteredMutator::new(
    //         filter_manager,
    //         ctx.clone(),
    //         StdMOptMutator::new(
    //             &mut state,
    //             tuple_list!(
    //                 GraphMutateDeleteOne::new(ctx.clone()),
    //                 GraphMutateIsolateComponent::new(ctx.clone()),
    //                 GraphMutateRegenerate::new(ctx.clone()),
    //                 GraphMutateAddOne::new(ctx.clone()),
    //             ),
    //             7,
    //             5,
    //         )?,
    //         10
    //     ),
    // );

    let graph_mutator = StdMutationalStage::new(
        GraphMutatorSelector::new(
            0.01,
            3,
            tuple_list!(
                GraphMutateDeleteOne::new(ctx.clone()),
                GraphMutateIsolateComponent::new(ctx.clone()),
                GraphMutateRegenerate::new(ctx.clone()),
                GraphMutateAddOne::new(ctx.clone()),
                GraphMutateCrossover::new(ctx.clone()),
            )
        )
    );

    let mut ms = MultiSampler::new(&ctx.pspec, EncoderConfig::default());

    let mut initial_seeds = vec![];
    for idx in 0..ctx.pspec.endpoints.len() {
        let initial_seed = ms.sample_with_constraints(&[SamplerConstraint::IncludeFunction(idx)]);
        if let Some(initial_seed) = initial_seed {
            initial_seeds.push(initial_seed.to_graph(&ctx.pspec, 0));
        }
    }

    println!("Initial seeds: {:?}", initial_seeds.len());

    // let graph_mutator = StdMutationalStage::new(
    //     GraphMutateSolver::new(
    //         ctx.pspec.clone(),
    //         ms,
    //         0.02
    //     )
    // );

    let context_stage = StdMutationalStage::new(
        GraphMutateBytes::new(ctx.clone(), mutator),
    );

    // let power = StdMutationalStage::new(
    //     GraphByteMutator::new(ctx.clone(), mutator),
    // );

    // A minimization+queue policy to get testcasess from the corpus
    let scheduler = IndexesLenTimeMinimizerScheduler::new(
        &edges_observer,
        StdWeightedScheduler::with_schedule(&mut state, &edges_observer, Some(PowerSchedule::fast())),
    );

    // let scheduler = QueueScheduler::new();

    // A fuzzer with feedbacks and a corpus scheduler
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);

    let runtime_metadata = Rc::new(RefCell::new(RuntimeMetadataView::new(RUNTIME_METADATA_PTR)));
    
    let metadata_logger = MetadataLoggerStage::new(
        runtime_metadata.clone(), ctx.clone(), runtime_metadata_path.to_string_lossy().to_string(), Duration::from_secs(600)
    );

    // The wrapped harness function, calling out to the LLVM-style harness
    let mut harness = |input: &GraphInput| {
        // Create a temporary directory for sandboxing
        let temp_dir = tempfile::tempdir().expect("Failed to create temporary directory");
        // let original_dir = std::env::current_dir().expect("Failed to get current directory");
        
        // Change to the temporary directory
        std::env::set_current_dir(&temp_dir.path()).expect("Failed to change to temporary directory");
        
        let mut local_view = (*runtime_metadata).borrow_mut();

        // Invoke the harness in the sandboxed environment
        let _res = gf_harness.invoke(&input.graph, Some(&mut local_view));

        ExitKind::Ok
    };

    let mut tracing_harness = harness;

    // Create the executor for an in-process function with one observer for edge coverage and one for the execution time
    // let mut executor = InProcessExecutor::with_timeout(
    //     &mut harness,
    //     tuple_list!(edges_observer, time_observer),
    //     &mut fuzzer,
    //     &mut state,
    //     &mut mgr,
    //     timeout,
    // )?;

    let mut executor = InProcessForkExecutor::new(
        &mut harness,
        tuple_list!(edges_observer, time_observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
        timeout,
        shmem_provider.clone()
    )?;

    // Setup a tracing stage in which we log comparisons
    let tracing = TracingStage::new(
        // InProcessExecutor::with_timeout(
        //     &mut tracing_harness,
        //     tuple_list!(cmplog_observer),
        //     &mut fuzzer,
        //     &mut state,
        //     &mut mgr,
        //     timeout * 10,
        // )?,

        InProcessForkExecutor::new(
            &mut tracing_harness,
            tuple_list!(cmplog_observer),
            &mut fuzzer,
            &mut state,
            &mut mgr,
            timeout * 10,
            shmem_provider
        )?,
        // Give it more time!
    );

    // The order of the stages matter!
    // let mut stages = tuple_list!(calibration, tracing, i2s, power);

    let mut stages = tuple_list!(
        metadata_logger,
        graph_mutator,
        tracing,
        i2s,
        context_stage,
    );

    // Read tokens
    if state.metadata_map().get::<Tokens>().is_none() {
        let mut toks = Tokens::default();
        if let Some(tokenfile) = tokenfile {
            toks.add_from_file(tokenfile)?;
        }
        #[cfg(any(target_os = "linux", target_vendor = "apple"))]
        {
            toks += autotokens()?;
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

    // If corpus is empty, add a seed
    if state.corpus().count() == 0 {
        for seed in initial_seeds {
            let seed_input = GraphInput {
                graph: seed,
            };
            fuzzer.add_input(&mut state, &mut executor, &mut mgr, seed_input)?;
        }
    }

    // Remove target output (logs still survive)
    // #[cfg(unix)]
    // {
    //     let null_fd = file_null.as_raw_fd();
    //     dup2(null_fd, io::stdout().as_raw_fd())?;
    //     if std::env::var("LIBAFL_FUZZBENCH_DEBUG").is_err() {
    //         dup2(null_fd, io::stderr().as_raw_fd())?;
    //     }
    // }
    // // reopen file to make sure we're at the end
    // log.replace(OpenOptions::new().append(true).create(true).open(logfile)?);

    println!("Starting fuzzing loop");
    fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)?;

    // Never reached
    Ok(())
}
