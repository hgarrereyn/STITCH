"""
Robust process management utilities for fuzzer and bucketizer processes.

This module provides a ProcessManager class that handles:
- Proper process spawning and monitoring
- Graceful termination with signal handling
- Child process cleanup (for forked processes)
- Health monitoring with automatic restart on crashes
- Process group management for better isolation
"""

import os
import signal
import subprocess
import time
import threading
import psutil
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any
from dataclasses import dataclass
from enum import Enum

from .colors import Colors


class ProcessState(Enum):
    """Process state enumeration."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASHED = "crashed"
    FAILED = "failed"


@dataclass
class ProcessConfig:
    """Configuration for a managed process."""
    name: str
    command: List[str]
    cwd: Optional[Path] = None
    env: Optional[Dict[str, str]] = None
    restart_on_crash: bool = True
    max_restarts: int = 5
    restart_delay: float = 2.0
    health_check_interval: float = 10.0
    health_check_timeout: float = 5.0
    log_file: Optional[Path] = None
    on_crash: Optional[Callable[[str], None]] = None
    on_restart: Optional[Callable[[str], None]] = None


class ProcessManager:
    """
    Manages processes with health monitoring, automatic restart, and proper cleanup.
    
    Features:
    - Process group management for better isolation
    - Health monitoring with configurable intervals
    - Automatic restart on crashes with exponential backoff
    - Proper signal handling for graceful shutdown
    - Child process cleanup (handles forked processes)
    - Logging and event callbacks
    """
    
    def __init__(self):
        self._processes: Dict[str, subprocess.Popen] = {}
        self._process_configs: Dict[str, ProcessConfig] = {}
        self._process_states: Dict[str, ProcessState] = {}
        self._monitor_threads: Dict[str, threading.Thread] = {}
        self._shutdown_event = threading.Event()
        self._restart_counts: Dict[str, int] = {}
        self._lock = threading.RLock()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print(f"\n{Colors.YELLOW}[*]{Colors.END} Received signal {signum}, shutting down processes...")
        self.shutdown()
    
    def add_process(self, config: ProcessConfig) -> None:
        """Add a process configuration to be managed."""
        with self._lock:
            self._process_configs[config.name] = config
            self._process_states[config.name] = ProcessState.STOPPED
            self._restart_counts[config.name] = 0
    
    def start_process(self, name: str) -> bool:
        """Start a managed process."""
        with self._lock:
            if name not in self._process_configs:
                print(f"{Colors.RED}[-]{Colors.END} Process '{name}' not configured")
                return False
            
            if self._process_states[name] in [ProcessState.STARTING, ProcessState.RUNNING]:
                print(f"{Colors.YELLOW}[*]{Colors.END} Process '{name}' already running")
                return True
            
            config = self._process_configs[name]
            self._process_states[name] = ProcessState.STARTING
            
            try:
                # Prepare environment
                env = os.environ.copy()
                if config.env:
                    env.update(config.env)
                
                # Prepare log file if specified
                stdout = stderr = None
                if config.log_file:
                    log_file = open(config.log_file, 'a')
                    stdout = stderr = log_file
                
                # Start process with process group for better isolation
                process = subprocess.Popen(
                    config.command,
                    cwd=config.cwd,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    # preexec_fn=os.setsid,  # Create new process group
                    start_new_session=True
                )
                
                self._processes[name] = process
                self._process_states[name] = ProcessState.RUNNING
                
                print(f"{Colors.GREEN}[+]{Colors.END} Started process '{name}' (PID: {process.pid})")
                
                # Start monitoring thread
                self._start_monitor(name)
                
                return True
                
            except Exception as e:
                print(f"{Colors.RED}[-]{Colors.END} Failed to start process '{name}': {e}")
                self._process_states[name] = ProcessState.FAILED
                return False
    
    def stop_process(self, name: str, timeout: float = 10.0) -> bool:
        """Stop a managed process gracefully."""
        with self._lock:
            if name not in self._processes:
                return True
            
            if self._process_states[name] in [ProcessState.STOPPED, ProcessState.STOPPING]:
                return True
            
            process = self._processes[name]
            self._process_states[name] = ProcessState.STOPPING
            
            try:
                # First try graceful termination
                print(f"{Colors.YELLOW}[*]{Colors.END} Stopping process '{name}' (PID: {process.pid})")
                
                # Send SIGTERM to the process group
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    # Process already dead
                    pass
                
                # Wait for graceful shutdown
                try:
                    process.wait(timeout=timeout)
                    print(f"{Colors.GREEN}[+]{Colors.END} Process '{name}' stopped gracefully")
                except subprocess.TimeoutExpired:
                    # Force kill if graceful shutdown fails
                    print(f"{Colors.YELLOW}[*]{Colors.END} Process '{name}' didn't stop gracefully, force killing...")
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        process.wait(timeout=2.0)
                        print(f"{Colors.GREEN}[+]{Colors.END} Process '{name}' force killed")
                    except (subprocess.TimeoutExpired, ProcessLookupError):
                        print(f"{Colors.RED}[-]{Colors.END} Failed to kill process '{name}'")
                
                # Clean up child processes
                self._cleanup_child_processes(process.pid)
                
            except Exception as e:
                print(f"{Colors.RED}[-]{Colors.END} Error stopping process '{name}': {e}")
            
            finally:
                self._process_states[name] = ProcessState.STOPPED
                if name in self._processes:
                    del self._processes[name]
                if name in self._monitor_threads:
                    del self._monitor_threads[name]
            
            return True
    
    def _cleanup_child_processes(self, parent_pid: int) -> None:
        """Clean up child processes that may have been forked."""
        try:
            parent = psutil.Process(parent_pid)
            children = parent.children(recursive=True)
            
            for child in children:
                try:
                    if child.is_running():
                        print(f"{Colors.YELLOW}[*]{Colors.END} Cleaning up child process {child.pid}")
                        child.terminate()
                        child.wait(timeout=2.0)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired, psutil.AccessDenied):
                    # Process already dead or inaccessible
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Parent process already dead
            pass
    
    def _start_monitor(self, name: str) -> None:
        """Start monitoring thread for a process."""
        def monitor():
            config = self._process_configs[name]
            last_health_check = time.time()
            
            while not self._shutdown_event.is_set():
                try:
                    with self._lock:
                        if name not in self._processes:
                            break
                        
                        process = self._processes[name]
                        if self._process_states[name] != ProcessState.RUNNING:
                            break
                    
                    # Check if process is still running
                    if process.poll() is not None:
                        # Process has terminated
                        with self._lock:
                            self._process_states[name] = ProcessState.CRASHED
                            print(f"{Colors.RED}[-]{Colors.END} Process '{name}' crashed (exit code: {process.returncode})")
                            
                            if config.on_crash:
                                config.on_crash(name)
                            
                            if config.restart_on_crash and self._restart_counts[name] < config.max_restarts:
                                self._restart_counts[name] += 1
                                print(f"{Colors.YELLOW}[*]{Colors.END} Restarting process '{name}' (attempt {self._restart_counts[name]}/{config.max_restarts})")
                                
                                if config.on_restart:
                                    config.on_restart(name)
                                
                                time.sleep(config.restart_delay * (2 ** (self._restart_counts[name] - 1)))  # Exponential backoff
                                
                                # Restart the process
                                self.stop_process(name, timeout=5.0)
                                if self.start_process(name):
                                    last_health_check = time.time()
                                    continue
                            else:
                                if self._restart_counts[name] >= config.max_restarts:
                                    print(f"{Colors.RED}[-]{Colors.END} Process '{name}' exceeded max restarts")
                                break
                    
                    # Periodic health check
                    current_time = time.time()
                    if current_time - last_health_check >= config.health_check_interval:
                        if not self._health_check(name, config):
                            with self._lock:
                                self._process_states[name] = ProcessState.CRASHED
                                print(f"{Colors.RED}[-]{Colors.END} Process '{name}' failed health check")
                                
                                if config.restart_on_crash and self._restart_counts[name] < config.max_restarts:
                                    self._restart_counts[name] += 1
                                    print(f"{Colors.YELLOW}[*]{Colors.END} Restarting process '{name}' due to health check failure")
                                    
                                    if config.on_restart:
                                        config.on_restart(name)
                                    
                                    time.sleep(config.restart_delay)
                                    self.stop_process(name, timeout=5.0)
                                    if self.start_process(name):
                                        last_health_check = time.time()
                                        continue
                                else:
                                    break
                        else:
                            last_health_check = current_time
                    
                    time.sleep(1.0)  # Check every second
                    
                except Exception as e:
                    print(f"{Colors.RED}[-]{Colors.END} Monitor error for process '{name}': {e}")
                    time.sleep(5.0)
        
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        self._monitor_threads[name] = thread
    
    def _health_check(self, name: str, config: ProcessConfig) -> bool:
        """Perform a health check on a process."""
        try:
            with self._lock:
                if name not in self._processes:
                    return False
                
                process = self._processes[name]
            
            # Check if process is responsive by sending a signal
            try:
                os.kill(process.pid, 0)  # Check if process exists
            except ProcessLookupError:
                return False
            
            # Additional health checks can be added here
            # For example, checking if log files are being updated,
            # or if specific files are being created
            
            return True
            
        except Exception as e:
            print(f"{Colors.RED}[-]{Colors.END} Health check failed for '{name}': {e}")
            return False
    
    def is_running(self, name: str) -> bool:
        """Check if a process is currently running."""
        with self._lock:
            return (name in self._processes and 
                   self._process_states[name] == ProcessState.RUNNING and
                   self._processes[name].poll() is None)
    
    def get_process_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get information about a managed process."""
        with self._lock:
            if name not in self._process_configs:
                return None
            
            info = {
                'name': name,
                'state': self._process_states[name].value,
                'restart_count': self._restart_counts[name],
                'pid': None,
                'returncode': None
            }
            
            if name in self._processes:
                process = self._processes[name]
                info['pid'] = process.pid
                info['returncode'] = process.returncode
            
            return info
    
    def list_processes(self) -> List[Dict[str, Any]]:
        """List all managed processes and their status."""
        with self._lock:
            return [self.get_process_info(name) for name in self._process_configs.keys()]
    
    def shutdown(self, timeout: float = 30.0) -> None:
        """Shutdown all managed processes."""
        print(f"{Colors.YELLOW}[*]{Colors.END} Shutting down process manager...")
        
        self._shutdown_event.set()
        
        # Stop all processes
        with self._lock:
            process_names = list(self._processes.keys())
        
        for name in process_names:
            self.stop_process(name, timeout=timeout / len(process_names) if process_names else timeout)
        
        print(f"{Colors.GREEN}[+]{Colors.END} Process manager shutdown complete")
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown()
