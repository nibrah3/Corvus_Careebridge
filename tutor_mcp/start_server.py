"""Entry point for running tutor_mcp as a module: python -m tutor_mcp.server"""
import sys
import os

sys.path.insert(0, "E:\\Corvus_Careebridge")
os.chdir("E:\\Corvus_Careebridge")

from tutor_mcp.server import mcp

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8716)
    args = parser.parse_args()
    mcp.run_http(args.port)
