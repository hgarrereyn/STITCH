use std::borrow::Cow;
use std::rc::Rc;
use std::cell::RefCell;
use std::time::{Duration, Instant};

use gf_core::filter::FilterManager;
use libafl::{
    stages::Stage,
    state::{UsesState, State},
    Error,
};
use libafl_bolts::Named;

pub struct FilterReloaderStage<S> {
    verbose: bool,
    filter_manager: Rc<RefCell<FilterManager>>,
    reload_interval: Duration,
    last_reload: Instant,
    phantom: std::marker::PhantomData<S>,
}

impl<S> FilterReloaderStage<S> {
    pub fn new(filter_manager: Rc<RefCell<FilterManager>>, reload_interval: Duration, verbose: bool) -> Self {
        Self {
            verbose,
            filter_manager,
            reload_interval,
            last_reload: Instant::now(),
            phantom: std::marker::PhantomData,
        }
    }
}

impl<S> Named for FilterReloaderStage<S> {
    fn name(&self) -> &Cow<'static, str> {
        static NAME: Cow<'static, str> = Cow::Borrowed("FilterReloaderStage");
        &NAME
    }
}

impl<S> UsesState for FilterReloaderStage<S>
where
    S: State,
{
    type State = S;
}

impl<E, EM, Z, S> Stage<E, EM, Z> for FilterReloaderStage<S>
where
    S: State,
    E: UsesState<State = S>,
    EM: UsesState<State = S>,
    Z: UsesState<State = S>,
{
    fn perform(
        &mut self,
        _fuzzer: &mut Z,
        _executor: &mut E,
        _state: &mut Self::State,
        _manager: &mut EM,
    ) -> Result<(), Error> {
        let now = Instant::now();
        if now.duration_since(self.last_reload) >= self.reload_interval {
            // if self.verbose {
            //     println!("Reloading filters");
            // }
            self.filter_manager.borrow_mut().reload(self.verbose);
            self.last_reload = now;
        }
        Ok(())
    }

    fn should_restart(&mut self, _state: &mut Self::State) -> Result<bool, Error> {
        Ok(true)
    }

    fn clear_progress(&mut self, _state: &mut Self::State) -> Result<(), Error> {
        Ok(())
    }
}
