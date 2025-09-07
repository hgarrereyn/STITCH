use libc::{c_int, c_void, sigaction, sigaddset, sigemptyset, SA_NODEFER, SA_ONSTACK, SA_SIGINFO};
use nix::sys::signal::{self, SaFlags, SigAction, SigHandler, SigSet, Signal};
use std::{ffi::CString, io::{self, Write}, ptr};


extern "C" fn on_sigsys(_sig: c_int, _info: *mut libc::siginfo_t, _uctx: *mut c_void) {
    // Keep this async-signal-safe: only use safe syscalls.
    // Optional one-line debug to stderr (also async-signal-safe).
    let msg = b"[seccomp] forbidden syscall -> raising SIGUSR2\n";
    unsafe {
        let _ = libc::write(libc::STDERR_FILENO, msg.as_ptr() as *const c_void, msg.len());
        // Send SIGUSR2 to ourselves so the parent can detect and respawn.
        libc::raise(libc::SIGUSR2);
        loop {
            libc::sleep(1);
        }
    }
    // Returning from the handler would resume, but SIGUSR2 is now pending and will terminate
    // the process with that signal immediately after we return.
}

pub unsafe fn install_sigsys_bridge_to_sigusr2() -> Result<(), &'static str> {
    let mut sa: sigaction = std::mem::zeroed();
    sigemptyset(&raw mut sa.sa_mask);
    sa.sa_flags = SA_NODEFER | SA_SIGINFO | SA_ONSTACK;
    sa.sa_sigaction = on_sigsys as usize;
    if sigaction(libc::SIGSYS as i32, &raw mut sa, ptr::null_mut()) < 0 {
        let err_str = CString::new("Failed to setup {libc::SIGSYS} handler").unwrap();
        libc::perror(err_str.as_ptr());
        return Err("Failed to setup {libc::SIGSYS} handler");
    }
    Ok(())
}

fn set_no_new_privs() -> io::Result<()> {
    // Required for unprivileged seccomp load
    let ret = unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) };
    if ret != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub fn install_seccomp_trap(block_these: &[&str]) -> Result<(), Box<dyn std::error::Error>> {
    use libseccomp::{ScmpAction, ScmpFilterContext, ScmpSyscall};

    // Allow everything by default; explicitly TRAP the syscalls you care about.
    let mut ctx = ScmpFilterContext::new(ScmpAction::Allow)?;

    for name in block_these {
        let sc = ScmpSyscall::from_name(name)?;
        // TRAP => kernel delivers SIGSYS to the thread that made the syscall.
        ctx.add_rule(ScmpAction::Trap, sc)?;
    }

    // If you want to synchronously apply to all existing threads, you can try enabling TSYNC.
    // Not strictly necessary if you load before spawning threads.
    // (This attribute is supported on recent libseccomp/kernels; ignore if not available.)
    // let _ = ctx.set_filter_attr(libseccomp::FilterAttr::CtlTsync, 1);

    // For unprivileged processes, PR_SET_NO_NEW_PRIVS must be set before load.
    set_no_new_privs()?;
    ctx.load()?; // install in the current thread (and children inherit)
    Ok(())
}
