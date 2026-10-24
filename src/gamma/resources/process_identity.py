import platform



def process_identity(pid):
    """Return 'boot-id:starttime' for a live Linux process, or 'dead'/'unknown'."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "unknown"

    if platform.system() != "Linux":
        return "unknown"

    try:
        with open("/proc/sys/kernel/random/boot_id", "r") as f:
            boot_id = f.read().strip()
    except (OSError, PermissionError):
        return "unknown"

    stat_path = "/proc/{}/stat".format(pid)
    try:
        with open(stat_path, "r") as f:
            data = f.read()
    except FileNotFoundError:
        return "dead"
    except (PermissionError, OSError):
        return "unknown"

    try:
        last_paren = data.rfind(")")
        if last_paren == -1:
            return "unknown"
        fields = data[last_paren + 1:].split()
        state = fields[0]        # field 3 (signal state)
        starttime = fields[19]  # field 22 (starttime)
    except (IndexError, ValueError):
        return "unknown"

    if state == "Z":
        return "dead"

    if not boot_id or not starttime.isdigit():
        return "unknown"
    return boot_id + ":" + starttime
