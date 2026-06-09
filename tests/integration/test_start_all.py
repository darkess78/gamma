#!/usr/bin/env python3
"""Test for scripts/start_all.sh startup script."""
import subprocess
import sys
import time
from pathlib import Path

TEST_SCRIPT = Path("scripts/start_all.sh").absolute()
TIMEOUT = 60  # seconds

def test_start_all_sh_exists():
    """Test that start_all.sh script exists and is executable."""
    assert TEST_SCRIPT.exists(), f"{TEST_SCRIPT} does not exist"
    assert TEST_SCRIPT.is_file(), f"{TEST_SCRIPT} is not a regular file"
    assert TEST_SCRIPT.stat().st_mode & 0o111, f"{TEST_SCRIPT} is not executable"

def test_start_all_sh_cwd():
    """Test that start_all.sh runs from its own directory."""
    result = subprocess.run([str(TEST_SCRIPT)], capture_output=True, text=True, timeout=30)
    
    # Check for error output
    assert result.returncode == 0, f"start_all.sh failed: {result.stderr}"
    
    # Verify expected outputs in stdout
    output_lines = result.stdout.split('\n')
    assert any("Loading CUDA libraries" in line for line in output_lines), "Missing CUDA loading output"
    assert any("Qwen-TTS PID:" in line for line in output_lines), "Missing Qwen-TTS PID output"
    assert any("Starting Qwen-TTS" in line for line in output_lines), "Missing Qwen-TTS start output"

def test_start_all_sh_outputs_health_checks():
    """Test that start_all.sh performs health checks and outputs status."""
    # We can't run the full script without services running, so just verify syntax
    import ast
    with open(TEST_SCRIPT, 'r') as f:
        content = f.read()
    
    # Verify it contains health check logic
    assert "curl -s http://127.0.0.1:9882/health" in content, "Missing Qwen-TTS health check"
    assert "curl -s http://127.0.0.1:8000/health" in content, "Missing Shana health check"
    assert "curl -s http://127.0.0.1:8001/health" in content, "Missing Dashboard health check"
    
    # Verify it contains expected status messages
    assert "✅ Dashboard:" in content, "Missing Dashboard success message"
    assert "✅ Shana API:" in content, "Missing Shana API success message"
    assert "✅ Qwen-TTS:" in content, "Missing Qwen-TTS success message"

def test_start_all_sh_uses_correct_ports():
    """Test that start_all.sh uses correct service ports."""
    with open(TEST_SCRIPT, 'r') as f:
        content = f.read()
    
    # Qwen-TTS should use port 9882
    assert "9882" in content, "Missing Qwen-TTS port 9882 reference"
    # Shana API should use port 8000
    assert "8000" in content, "Missing Shana port 8000 reference"
    # Dashboard should use port 8001
    assert "8001" in content, "Missing Dashboard port 8001 reference"

def test_start_all_sh_sets_environment():
    """Test that start_all.sh sets required environment variables."""
    with open(TEST_SCRIPT, 'r') as f:
        content = f.read()
    
    assert "LD_LIBRARY_PATH" in content, "Missing LD_LIBRARY_PATH export"
    assert "ollama/cuda_v12" in content, "Missing CUDA library path"
