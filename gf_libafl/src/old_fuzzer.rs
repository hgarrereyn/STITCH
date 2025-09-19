use core::{cell::RefCell, time::Duration};
#[cfg(unix)]
use std::os::unix::io::{AsRawFd, FromRawFd};
use std::{
    borrow::BorrowMut, fs::{self, File, OpenOptions}, io::{self, Write}, num::NonZeroUsize, path::PathBuf, process, rc::Rc, str::Bytes, sync::Arc
};
use libc::raise;
use tempfile;

use libafl::{
    corpus::{Corpus, InMemoryOnDiskCorpus, OnDiskCorpus}, events::{EventRestarter, SimpleEventManager, SimpleRestartingEventManager}, executors::{hooks::ExecutorHooksTuple, inprocess::{GenericInProcessExecutor, HookableInProcessExecutor, InProcessExecutor}, inprocess_fork::GenericInProcessForkExecutor, ExitKind, ForkserverExecutor, InProcessForkExecutor}, feedback_or, feedbacks::{CrashFeedback, MaxMapFeedback, TimeFeedback}, fuzzer::{Fuzzer, StdFuzzer}, inputs::BytesInput, monitors::SimpleMonitor, mutators::{
        havoc_mutations_no_crossover, token_mutations::I2SRandReplace, tokens_mutations, StdMOptMutator, StdScheduledMutator, Tokens
    }, observers::{CanTrack, HitcountsMapObserver, StdMapObserver, TimeObserver}, schedulers::{powersched::PowerSchedule, IndexesLenTimeMinimizerScheduler, QueueScheduler, StdWeightedScheduler}, stages::{
        StdMutationalStage,
        TracingStage,
    }, state::{HasCorpus, StdState}, Error, Evaluator, HasMetadata
};
use libafl_bolts::{
    current_time, os::peak_rss_mb_child_processes, rands::StdRand, shmem::{ShMemProvider, StdShMemProvider}, tuples::{tuple_list, Merge}, AsSlice, AsSliceMut
};
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use libafl_targets::autotokens;
use libafl_targets::{
    edges_max_num, libfuzzer_initialize, std_edges_map_observer, CmpLogObserver, EDGES_MAP_ALLOCATED_SIZE, EDGES_MAP_DEFAULT_SIZE, EDGES_MAP_PTR
};
#[cfg(unix)]
use libafl_bolts::{dup_and_mute_outputs, os::dup2};

use simple_logger::SimpleLogger;
use log::LevelFilter;

use crate::{components::{graph_input::GraphInput, measure_stage::{IndexesBailedLenMinimizerScheduler, MarkStage}, mutate_bytes::GraphMutateBytes, mutate_graph::{GraphMutateAddOne, GraphMutateCrossover, GraphMutateDeleteOne, GraphMutateIsolateComponent, GraphMutateRegenerate, GraphMutatorSelector}, runtime_metadata::MetadataLoggerStage, state_metadata::{StateMetadata, StateMode}}, seccomp::{install_seccomp_trap, install_sigsys_bridge_to_sigusr2}};
use gf_core::{harness::{Harness, InvokeResult}, mutation::MutationContext, pspec::PSpec, runtime_metadata::{RuntimeMetadata, RuntimeMetadataView}};

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
    rss_limit_mb: Option<u64>,
    keep_stdout: bool,
    verbose: bool
) -> Result<(), Error>
{
    if verbose {
        SimpleLogger::new().with_level(LevelFilter::Info).init().unwrap();
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
            (io::stdout().as_raw_fd(), io::stderr().as_raw_fd())
        } else {
            unsafe { dup_and_mute_outputs()? }
        };

        // If we are debugging, re-enable target stderror.
        if std::env::var("LIBAFL_FUZZBENCH_DEBUG").is_ok() {
            // # Safety
            // Nobody else uses the new stderror here.
            unsafe {
                dup2(new_stderr, io::stderr().as_raw_fd())?;
            }
        }

        // # Safety
        // The new stdout is open at this point, and we will don't use it anywhere else.
        #[cfg(unix)]
        unsafe {
            File::from_raw_fd(new_stdout)
        }
    };

    #[cfg(unix)]
    let file_null = File::open("/dev/null").unwrap();

    // 'While the monitor are state, they are usually used in the broker - which is likely never restarted
    let monitor = SimpleMonitor::with_user_monitor(|s| {
        #[cfg(unix)]
        writeln!(&mut stdout_cpy, "{s}").unwrap();
        #[cfg(windows)]
        println!("{s}");
        writeln!(log.borrow_mut(), "{:?} {s}", current_time()).unwrap();
        // println!("{s}")
    });

    // We need a shared map to store our state before a crash.
    // This way, we are able to continue fuzzing afterwards.
    let mut shmem_provider = StdShMemProvider::new()?;

    let mut shmem;
    unsafe{
        shmem = shmem_provider.new_shmem(EDGES_MAP_ALLOCATED_SIZE).unwrap();
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

    let mut invoke_result_shmem = shmem_provider.new_shmem(1).unwrap();
    let invoke_result_shmem_buf = invoke_result_shmem.as_slice_mut();
    
    let obs = unsafe { std_edges_map_observer("edges") };

    // let obs = unsafe { StdMapObserver::from_mut_ptr("edges", EDGES_MAP_PTR, edges_max_num()) };

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

    let mut initial_seeds = vec![];
    for idx in ctx.cache.borrow().enabled_endpoints.iter() {
        initial_seeds.push(ctx.generate_seeded(*idx));
    }

    println!("Initial seeds: {:?}", initial_seeds.len());

    let context_stage = StdMutationalStage::new(
        GraphMutateBytes::new(ctx.clone(), mutator),
    );

    let scheduler = IndexesBailedLenMinimizerScheduler::new(
        &edges_observer,
        StdWeightedScheduler::with_schedule(&mut state, &edges_observer, Some(PowerSchedule::fast())),
    );

    // A fuzzer with feedbacks and a corpus scheduler
    let mut fuzzer = StdFuzzer::new(scheduler, feedback, objective);

    let runtime_metadata = Rc::new(RefCell::new(RuntimeMetadataView::new(RUNTIME_METADATA_PTR)));

    let invoke_result = Rc::new(RefCell::new(invoke_result_shmem_buf));

    // The wrapped harness function, calling out to the LLVM-style harness
    let mut harness = |input: &GraphInput| {
        // Create a temporary directory for sandboxing
        // let temp_dir = tempfile::tempdir().expect("Failed to create temporary directory");
        // let original_dir = std::env::current_dir().expect("Failed to get current directory");
        
        // Change to the temporary directory
        // std::env::set_current_dir(&temp_dir.path()).expect("Failed to change to temporary directory");
        
        let mut local_view = (*runtime_metadata).borrow_mut();

        // Invoke the harness in the sandboxed environment
        let res = gf_harness.invoke(&input.graph, Some(&mut local_view));

        // Store the result in the shared memory
        (*invoke_result).borrow_mut()[0] = matches!(res, InvokeResult::Success) as u8;

        match res {
            InvokeResult::Success => ExitKind::Ok,
            InvokeResult::Bailed(_) => ExitKind::Ok,
            InvokeResult::AssertionViolation(_, msg) => {
                // abort with the message
                unsafe {
                    panic!("{}", msg);
                }
            },
            InvokeResult::LibraryException(_, _) => ExitKind::Ok,
        }
    };

    let mut tracing_harness = harness;

    // Create the executor for an in-process function with one observer for edge coverage and one for the execution time
    let mut executor = InProcessExecutor::with_timeout(
        &mut harness,
        tuple_list!(edges_observer, time_observer),
        &mut fuzzer,
        &mut state,
        &mut mgr,
        timeout,
    )?;

    // let mut executor = InProcessForkExecutor::new(
    //     &mut harness,
    //     tuple_list!(edges_observer, time_observer),
    //     &mut fuzzer,
    //     &mut state,
    //     &mut mgr,
    //     timeout,
    //     shmem_provider.clone()
    // )?;

    // Setup a tracing stage in which we log comparisons
    let tracing = TracingStage::new(
        InProcessExecutor::with_timeout(
            &mut tracing_harness,
            tuple_list!(cmplog_observer),
            &mut fuzzer,
            &mut state,
            &mut mgr,
            timeout * 10,
        )?,

        // InProcessForkExecutor::new(
        //     &mut tracing_harness,
        //     tuple_list!(cmplog_observer),
        //     &mut fuzzer,
        //     &mut state,
        //     &mut mgr,
        //     timeout * 10,
        //     shmem_provider
        // )?,
        // Give it more time!
    );

    // let mark = MarkStage::new(invoke_result.clone());

    // The order of the stages matter!
    // let mut stages = tuple_list!(calibration, tracing, i2s, power);

    let mut stages = tuple_list!(
        // mark,
        // metadata_logger,
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

    // reopen file to make sure we're at the end
    log.replace(OpenOptions::new().append(true).create(true).open(logfile)?);

    // Enforce address space hard limit based on rss_limit_mb to avoid K8s OOM kills.
    // Rely on LibAFL's in-process crash handlers for proper restart/crash reporting.
    if let Some(rss_limit_mb) = rss_limit_mb {
        if (rss_limit_mb > 0) {
            let limit_bytes: u64 = rss_limit_mb * 1024 * 1024;
            let mut rlimit = libc::rlimit {
                rlim_cur: limit_bytes as libc::rlim_t,
                rlim_max: libc::RLIM_INFINITY,
            };
            let sr = unsafe { libc::setrlimit(libc::RLIMIT_AS, &rlimit) };
            if sr != 0 {
                eprintln!("Warning: setrlimit(RLIMIT_AS) failed: {}", std::io::Error::last_os_error());
            }
        }
    }

    log::info!("Installing seccomp trap");
    // Install seccomp trap
    unsafe { install_sigsys_bridge_to_sigusr2().unwrap(); }
    install_seccomp_trap(&[
        "execve", "execveat",
        "fork", "vfork",
        "clone", "clone3",
        "rt_sigaction",
        // "rt_sigprocmask",
    ]).unwrap();

    log::info!("Seccomp trap installed");

    log::info!("Seeding");
    // Seed loading
    if state.metadata_map().get::<StateMetadata>().is_none() {
        state.add_metadata(StateMetadata::new());
    }

    let state_metadata = state.metadata_map().get::<StateMetadata>().unwrap().clone();
    match state_metadata.mode {
        StateMode::Initializing => {
            log::info!("First run");
            for (idx, seed) in initial_seeds.iter().enumerate() {
                log::info!("Adding seed {}/{}", idx + 1, initial_seeds.len());
                let seed_input = GraphInput {
                    graph: seed.clone(),
                };
                state.metadata_map_mut().get_mut::<StateMetadata>().unwrap().mode = StateMode::AddingSeed(idx);
                fuzzer.add_input(&mut state, &mut executor, &mut mgr, seed_input)?;
            }
            state.metadata_map_mut().get_mut::<StateMetadata>().unwrap().mode = StateMode::Running;
        }
        StateMode::AddingSeed(crashed_idx) => {
            log::info!("Fuzzer crashed while adding seed {}/{}", crashed_idx + 1, initial_seeds.len());
            // Continue where it left off
            for (idx, seed) in initial_seeds.iter().enumerate() {
                if idx <= crashed_idx {
                    continue;
                }
                log::info!("Adding seed {}/{}", idx + 1, initial_seeds.len());
                let seed_input = GraphInput {
                    graph: seed.clone(),
                };
                state.metadata_map_mut().get_mut::<StateMetadata>().unwrap().mode = StateMode::AddingSeed(idx);
                fuzzer.add_input(&mut state, &mut executor, &mut mgr, seed_input)?;
            }
            state.metadata_map_mut().get_mut::<StateMetadata>().unwrap().mode = StateMode::Running;
        }
        StateMode::Running => {}
    }

    log::info!("Starting fuzzing loop");
    fuzzer.fuzz_loop(&mut stages, &mut executor, &mut state, &mut mgr)?;

    // Never reached
    Ok(())
}
