import subprocess
import os
import psutil
import base64
import shutil

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

def list_directory(directory: str) -> str:
    """Lists contents of a directory."""
    try:
        path = os.path.abspath(directory)
        if not os.path.exists(path):
            return f"Error: Directory not found at {path}"
        if not os.path.isdir(path):
            return f"Error: {path} is not a directory."
        
        items = os.listdir(path)
        if not items:
            return "Directory is empty."
            
        result = [f"Contents of {path}:"]
        for item in items:
            item_path = os.path.join(path, item)
            size = os.path.getsize(item_path) if os.path.isfile(item_path) else "<DIR>"
            result.append(f"{size}\t{item}")
        return "\n".join(result)
    except Exception as e:
        return f"Error listing directory: {str(e)}"

def write_file(filepath: str, content: str, overwrite: bool = False) -> str:
    """Writes text to a local file."""
    try:
        path = os.path.abspath(filepath)
        if os.path.exists(path) and not overwrite:
            return f"Error: File already exists at {path} and overwrite=False."
            
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote {len(content)} characters to {path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"

def delete_file(filepath: str) -> str:
    """Deletes a specific local file."""
    try:
        path = os.path.abspath(filepath)
        if not os.path.exists(path):
            return f"Error: File not found at {path}"
        if os.path.isdir(path):
            return f"Error: {path} is a directory, not a file."
        os.remove(path)
        return f"Successfully deleted {path}"
    except Exception as e:
        return f"Error deleting file: {str(e)}"

def take_screenshot() -> list:
    """Captures the primary monitor and returns Anthropic image block list."""
    try:
        from PIL import ImageGrab
        import io
        
        # Grab all screens (handles hardware accelerated windows natively on Windows)
        img = ImageGrab.grab(all_screens=True, include_layered_windows=True)
        
        # Convert to PNG bytes in memory
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        png_bytes = buffer.getvalue()
        
        b64_data = base64.b64encode(png_bytes).decode("utf-8")
        
        return [
            {"type": "text", "text": "Screenshot captured successfully."},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}}
        ]
    except Exception as e:
        return f"Error taking screenshot: {str(e)}"

# Map tool names to functions
TOOL_REGISTRY = {
    "run_shell": run_shell,
    "read_file": read_file,
    "system_status": system_status,
    "list_directory": list_directory,
    "write_file": write_file,
    "delete_file": delete_file,
    "take_screenshot": take_screenshot
}
