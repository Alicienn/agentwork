---
trigger: always_on
description: Autonomous Driver and Worker operating rules for Antigravity (agy)
---
# Autonomous Execution Rules for Antigravity (agy)

1. **Use Native Tools for File Operations**: Always use native tools (`view_file`, `write_to_file`, `replace_file_content`) instead of spawning shell `cat`, `head`, `tail`, `grep`, `find`, or `sed` via `run_command`.
2. **Continuous Driver Execution**: In Phases 1, 2, and 4, drive the repository setup and reporting without stopping for intermediate confirmation. In Phase 3, delegate the build to `python3 agentwork/pipeline.py drive`.
3. **Respect Privacy Profile**: Check `agentwork/config/project.yaml` before performing any operations; never invoke `claude-or` if `routing_profile: sensitive`.
4. **Never Push**: Commit locally on dedicated branches; never run `git push`.
