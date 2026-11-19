default:
    @just --list

# Start Swift MCP and launch and connect the quickstart Workshop.
quickstart-serve:
    exec bash scripts/quickstart.sh

# Run Copilot in the quickstart Workshop using the caller key identified by KEY_ID.
quickstart-copilot:
    exec bash scripts/quickstart-copilot.sh
