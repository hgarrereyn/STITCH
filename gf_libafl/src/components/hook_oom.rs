use libafl::{executors::{hooks::ExecutorHook, HasObservers}, inputs::UsesInput};



pub struct HookOOM {
    pub mem_limit: u64,
}

impl HookOOM {
    pub fn new(mem_limit: u64) -> Self {
        Self { mem_limit }
    }
}

impl<S> ExecutorHook<S> for HookOOM
where
    S: UsesInput,
{
    fn init<E: HasObservers>(&mut self, state: &mut S) {
        // Do nothing
    }

    fn pre_exec(&mut self, state: &mut S, input: &S::Input) {
        // Set the memory limit with libc api
        // let rlimit = libc::rlimit {
        //     rlim_cur: self.mem_limit,
        //     rlim_max: self.mem_limit,
        // };
        // unsafe {
        //     libc::setrlimit(libc::RLIMIT_AS, &rlimit);
        // }

        // set ASAN_OPTIONS=hard_rss_limit_mb=256
        // add it to the existing ASAN_OPTIONS
        // let mut asan_options = std::env::var("ASAN_OPTIONS").unwrap_or_default();
        // asan_options = format!("{}:hard_rss_limit_mb={}", asan_options, self.mem_limit);
        // std::env::set_var("ASAN_OPTIONS", asan_options);
    }

    fn post_exec(&mut self, state: &mut S, input: &S::Input) {
        // Do nothing
    }
}