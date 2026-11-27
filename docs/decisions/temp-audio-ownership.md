# Temporary audio ownership

Each service owns a randomly named directory under the configured temporary directory.
This prevents another service's startup cleanup from deleting active playback input.
On POSIX, startup can reclaim a recognized namespace only when its owner file matches
the PID in its name and the operating system reports that PID does not exist. A reused
PID, unreadable owner, unexpected error, or missing owner leaves the directory intact.
On Windows the owner is unknown: `os.kill(pid, 0)` is destructive there, so abandoned
directories are retained rather than risking another process. Normal shutdown removes
the service's own directory on every platform.

Previous versions wrote loose `vox_*.wav` files with no owner information. Age does not
prove that an older instance has stopped using one, so automatic migration cannot safely
delete them. Operators can remove these files and abandoned namespaces after stopping all
instances; the README explains how to find the configured directory. This favors safety
over automatic cleanup after crashes and upgrades.
