"""
Helper functions for creating temporary files and directories in tests.
"""

import os
import tempfile


def create_temp_file_with_content(content, suffix=".txt", mode="w"):
    """Create a temporary file with specified content.

    Args:
        content: Content to write to the file
        suffix: File suffix (default: '.txt')
        mode: File mode (default: 'w')

    Returns:
        str: Path to the created temporary file
    """
    temp_file = tempfile.NamedTemporaryFile(mode=mode, suffix=suffix, delete=False)
    temp_file.write(content)
    temp_file.close()
    return temp_file.name


def cleanup_temp_file(file_path):
    """Clean up a temporary file.

    Args:
        file_path: Path to the temporary file to remove
    """
    if os.path.exists(file_path):
        os.unlink(file_path)
