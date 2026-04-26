import subprocess
import os
import psutil

def run_shell(command: str) -> str:
    """Executes a shell command and returns the output."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR]:\n{result.stderr}"
        return output if output else "Command executed successfully with no output."
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error executing command: {str(e)}"

def read_file(filepath: str) -> str:
    """Reads a local file."""
    try:
        path = os.path.abspath(filepath)
        if not os.path.exists(path):
            return f"Error: File not found at {path}"
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        return f"Error: {filepath} appears to be a binary file."
    except Exception as e:
        return f"Error reading file: {str(e)}"

def system_status() -> dict:
    """Returns local system status."""
    battery = psutil.sensors_battery()
    bat_percent = battery.percent if battery else "Unknown"
    bat_plugged = battery.power_plugged if battery else "Unknown"
    
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "ram_percent": psutil.virtual_memory().percent,
        "battery_percent": bat_percent,
        "is_plugged_in": bat_plugged,
        "platform": os.name
    }

# Map tool names to functions
TOOL_REGISTRY = {
    "run_shell": run_shell,
    "read_file": read_file,
    "system_status": system_status
}
